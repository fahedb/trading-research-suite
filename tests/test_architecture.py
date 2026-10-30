"""Layering rules, enforced rather than documented.

Architectural constraints that are only written down decay silently. These
tests parse the source and fail the build when a layer reaches somewhere it
should not.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import trading_research

if TYPE_CHECKING:
    from types import ModuleType

PACKAGE_ROOT = Path(trading_research.__file__).parent
ROOT_NAME = "trading_research"

# Which layers each layer is allowed to import from. ``core`` depends on
# nothing internal; ``ops`` is the Phase-5 composition root.  The disposable
# index may read experiment projections, but that dependency is one-way.
ALLOWED: dict[str, set[str]] = {
    "artifacts": {"core"},
    "core": set(),
    "data": {"core"},
    "features": {"core"},
    "outcomes": {"core"},
    "representations": {"core"},
    "models": {"core"},
    "decisions": {"core"},
    "evaluation": {"core"},
    "simulation": {"core"},
    "experiments": {
        "artifacts",
        "core",
        "data",
        "features",
        "representations",
        "models",
        "outcomes",
        "decisions",
        "evaluation",
        "simulation",
    },
    "index": {"core", "experiments"},
    "ops": {"core", "experiments", "index"},
}


def _internal_imports(source: Path) -> set[str]:
    """Return the ``trading_research`` layer names imported by one file."""
    tree = ast.parse(source.read_text(), filename=str(source))
    modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)

    layers = set()
    for module in modules:
        parts = module.split(".")
        if parts[0] == ROOT_NAME and len(parts) > 1:
            layers.add(parts[1])
    return layers


def _layer_files(layer: str) -> list[Path]:
    return sorted((PACKAGE_ROOT / layer).rglob("*.py"))


def test_every_declared_layer_exists() -> None:
    for layer in ALLOWED:
        assert (PACKAGE_ROOT / layer).is_dir(), f"missing layer package: {layer}"


def test_core_depends_on_no_other_layer() -> None:
    """The constraint that matters most: core stays free of implementations."""
    violations: list[str] = []
    for path in _layer_files("core"):
        for layer in _internal_imports(path):
            if layer != "core":
                violations.append(f"{path.relative_to(PACKAGE_ROOT)} imports {layer}")
    assert not violations, "core must not depend on other layers: " + "; ".join(violations)


def test_core_never_imports_a_concrete_component() -> None:
    """No registry lookups, no strategy names, nothing instantiable."""
    banned = ("RuleModel", "SimilarityModel", "RESEARCH_MODELS", "DECISION_POLICIES")
    for path in _layer_files("core"):
        text = path.read_text()
        for name in banned:
            assert name not in text, f"{path.name} references concrete component {name}"


@pytest.mark.parametrize("layer", sorted(ALLOWED))
def test_layer_respects_its_dependency_budget(layer: str) -> None:
    permitted = ALLOWED[layer] | {layer}
    violations: list[str] = []

    for path in _layer_files(layer):
        for imported in _internal_imports(path):
            if imported not in permitted:
                violations.append(f"{path.relative_to(PACKAGE_ROOT)} -> {imported}")

    assert not violations, f"{layer} exceeded its dependency budget: " + "; ".join(violations)


def test_only_decisions_and_core_name_a_trading_direction() -> None:
    """Direction vocabulary must not leak into research or feature layers."""
    research_layers = ["data", "features", "outcomes", "representations", "models"]
    for layer in research_layers:
        for path in _layer_files(layer):
            text = path.read_text()
            assert "Direction" not in text, (
                f"{path.relative_to(PACKAGE_ROOT)} references Direction; "
                "trading vocabulary belongs in the decisions layer"
            )


def test_the_package_imports_without_side_effects() -> None:
    """Importing a layer must not require optional dependencies or I/O."""
    import importlib

    for layer in sorted(ALLOWED):
        importlib.import_module(f"{ROOT_NAME}.{layer}")


# --------------------------------------------------------------- vendor neutrality

# Naming a venue in ``core`` is how a general platform quietly becomes a
# single-broker one. Adapter-specific vocabulary (field names, server names,
# terminal names) belongs to the adapter that reads that format.
VENDOR_TERMS = ("mt5", "metatrader", "justmarkets")


def test_core_names_no_data_vendor() -> None:
    """Core must not know which venue produced the data — not even in prose."""
    violations: list[str] = []
    for path in _layer_files("core"):
        text = path.read_text().lower()
        for term in VENDOR_TERMS:
            if term in text:
                violations.append(f"{path.relative_to(PACKAGE_ROOT)} mentions {term!r}")
    assert not violations, "core must stay vendor-neutral: " + "; ".join(violations)


# ------------------------------------------------------------ native time purity

# The modules that define the time axis and dataset identity. These two carry
# the platform's "no untagged time" and "identity is not a path" invariants,
# and both are implementable with the standard library alone.
NATIVE_PRIMITIVE_MODULES = ("time.py", "identity.py")

# Importing any of these into the native time module would mean a civil-time
# conversion is happening where the platform promises none can.
CIVIL_TIME_MODULES = {"datetime", "time", "calendar", "zoneinfo", "pytz", "dateutil"}

# DataFrame libraries whose semantics must never reach a public contract
# (ADR-0003). Deliberately excludes PyArrow and NumPy: the data layer will use
# PyArrow internally, and read-only NumPy arrays are the accepted public
# column contract, so banning those two here would have to be weakened later.
FORBIDDEN_FRAME_LIBRARIES = {"pandas", "polars", "duckdb"}

# NumPy arrays are the accepted public column contract.  The libraries below
# may be implementation details, but their types may not cross a public core
# or data-layer signature.  This is deliberately a separate set from
# ``FORBIDDEN_FRAME_LIBRARIES``: PyArrow belongs inside the Slice 1D adapter,
# while NumPy belongs on MarketFrame's public surface.
FORBIDDEN_PUBLIC_TYPE_LIBRARIES = {"pyarrow", "pandas", "polars", "duckdb"}

LOADER_IMPLEMENTATION_MODULES = ("data/integrity.py", "data/adapters/mt5.py")

MARKET_FRAME_PUBLIC_SURFACE = {
    "dataset_ref",
    "clock_domain",
    "unit",
    "schema",
    "num_rows",
    "keys",
    "column_names",
    "column",
    "select",
    "slice_by_keys",
}

FEATURE_REQUEST_PUBLIC_SURFACE = {
    "clock_domain",
    "column",
    "eval_length",
    "eval_offset",
    "from_segments",
    "keys",
    "source_identities",
    "unit",
}

FEATURE_FRAME_PUBLIC_SURFACE = {
    "clock_domain",
    "column",
    "column_names",
    "keys",
    "num_rows",
    "origin",
    "schema",
    "source_identities",
    "unit",
}

WINDOW_SPEC_PUBLIC_FIELDS = {
    "gap_policy",
    "max_prior_rows",
    "nominal_interval",
}

FEATURE_ORIGIN_PUBLIC_FIELDS = {
    "params",
    "provider_id",
    "version",
    "window",
}

PHASE3_RESEARCH_EXPORTS = {
    "BoundaryPolicy",
    "CorpusModelInputs",
    "MetricValue",
    "ModelInputs",
    "OutcomeRequest",
    "StageOrigin",
    "ToleranceSpec",
    "ViewSpan",
}

PHASE3_FRAME_EXPORTS = {
    "FittedPayload",
    "FittedState",
    "IntentFrame",
    "ModelOutputFrame",
    "OutcomeFrame",
    "RepresentationFrame",
    "TrainingTargetFrame",
}

PHASE3_PROTOCOL_EXPORTS = {
    "CorpusResearchModel",
    "DecisionPolicy",
    "Evaluator",
    "FeatureProvider",
    "OutcomeSpec",
    "RepresentationModel",
    "ResearchModel",
    "TradeEvaluator",
}

PHASE4_SIMULATION_EXPORTS = {
    "VERIFIED_COST_BINDING",
    "CommissionBasis",
    "CostSemanticsStatus",
    "EquityCurve",
    "ExecutionCostBinding",
    "ExecutionPriceModel",
    "ExecutionSpec",
    "SimulationRequest",
    "TradeExitReason",
    "TradeLog",
}

PHASE4_SIMULATION_REQUEST_SURFACE = {
    "clock_domain",
    "close",
    "high",
    "keys",
    "low",
    "num_rows",
    "open",
    "source_identities",
    "unit",
}

PHASE4_TRADE_LOG_SURFACE = {
    "ambiguous_exit",
    "clock_domain",
    "commission_cost",
    "direction",
    "entry_key",
    "entry_price",
    "execution_rows",
    "execution_spec",
    "exit_key",
    "exit_price",
    "exit_reason",
    "fold_index",
    "gapped_exit",
    "gross_pnl",
    "intent_rows_absent",
    "intent_rows_abstain",
    "intent_rows_flat",
    "intent_rows_long",
    "intent_rows_short",
    "intent_schema_id",
    "net_pnl",
    "num_trades",
    "policy_origin",
    "source_identities",
    "spread_cost",
    "stop_loss_price",
    "take_profit_price",
    "unexecuted_final_row_intents",
    "unit",
}

PHASE4_EQUITY_CURVE_SURFACE = {
    "clock_domain",
    "equity",
    "fold_index",
    "keys",
    "num_rows",
    "policy_origin",
    "source_identities",
    "unit",
}

PHASE4_ADVERSARIAL_TEST_PATH = Path(__file__).with_name("test_simulation_adversarial_controls.py")

PHASE4_ADVERSARIAL_CONTROL_CLASSES = frozenset(
    {
        "BeyondBoundaryFillControl",
        "BracketCarryForwardControl",
        "BracketMutationControl",
        "BracketPriceThroughGapControl",
        "CrossFoldCarryControl",
        "DecisionRowFillControl",
        "FractionalSignRoundingControl",
        "FutureRowExitControl",
        "SingleCostChargeControl",
        "TakeProfitWinsAmbiguityControl",
    }
)

PHASE4_CANARY_TEST_PATH = Path(__file__).with_name("test_simulation_canaries.py")

PHASE4_CANARY_IDS = frozenset({"K1_ORACLE", "K2_RANDOM", "K3_FLAT", "K4_ABSTAIN"})

PHASE4_PERFORMANCE_TEST_PATH = (
    Path(__file__).parent / "performance" / "test_phase4_simulation_budget.py"
)

PHASE3_DENSE_FRAME_SURFACE = {
    "clock_domain",
    "column",
    "column_names",
    "keys",
    "num_rows",
    "origin",
    "schema",
    "source_identities",
    "unit",
}

PHASE3_PROTOCOL_SURFACES = {
    "OutcomeSpec": {
        "boundary_policy",
        "comparison_tolerance",
        "compute",
        "decision_delay_rows",
        "horizon_rows",
        "outcome_id",
        "params",
        "reference_price_column",
        "required_columns",
        "target_column_names",
        "version",
    },
    "ResearchModel": {
        "comparison_tolerance",
        "cost_hint",
        "fit",
        "infer",
        "model_id",
        "output_column_names",
        "params",
        "required_feature_names",
        "required_representation_names",
        "version",
    },
    "CorpusResearchModel": {
        "comparison_tolerance",
        "cost_hint",
        "fit_corpus",
        "infer",
        "model_id",
        "output_column_names",
        "params",
        "required_feature_names",
        "required_representation_names",
        "version",
    },
    "RepresentationModel": {
        "comparison_tolerance",
        "cost_hint",
        "encode",
        "fit",
        "output_column_names",
        "params",
        "representation_id",
        "required_feature_names",
        "version",
    },
    "DecisionPolicy": {
        "decide",
        "intent_schema_id",
        "params",
        "policy_id",
        "required_output_columns",
        "version",
    },
    "Evaluator": {
        "comparison_tolerance",
        "evaluate",
        "evaluator_id",
        "metric_names",
        "params",
        "required_outcome_columns",
        "required_output_columns",
        "version",
    },
    "TradeEvaluator": {
        "comparison_tolerance",
        "evaluate",
        "evaluator_id",
        "metric_names",
        "params",
        "version",
    },
}

SLICE_3B_NEGATIVE_CONTROL_CLASSES = frozenset(
    {
        "AlignmentInversionOutcomeControl",
        "ClosureInferringOutcomeControl",
        "OffByOneOutcomeControl",
        "OverreachOutcomeControl",
        "TruncatingOutcomeControl",
    }
)

SLICE_3B_OUTCOME_SURFACE = {
    "boundary_policy",
    "comparison_tolerance",
    "compute",
    "decision_delay_rows",
    "horizon_rows",
    "outcome_id",
    "params",
    "reference_price_column",
    "required_columns",
    "target_column_names",
    "version",
}

SLICE_3C_LAYER_INVENTORIES = {
    "models": {
        "__init__.py",
        "execution.py",
        "reference.py",
        "registry.py",
        "uls_entry_selector.py",
        "uls_joint_selector.py",
        "uls_tp_selector.py",
        "zone_selector.py",
    },
    "representations": {"__init__.py", "execution.py", "reference.py", "registry.py"},
}

SLICE_3C_MODEL_CLASSES = {
    "gap_sensitive_gate_v1": "GapSensitiveGateV1",
    "multi_confluence_gate_v1": "MultiConfluenceGateV1",
    "reference_library_v1": "ReferenceLibraryModel",
    "stateless_rule_v1": "StatelessRuleModel",
    "supervised_mean_v1": "SupervisedMeanModel",
    "uls_push_gate_v1": "UlsPushGateV1",
    "zone_event_gate_v1": "ZoneEventGateV1",
}

SLICE_3C_REPRESENTATION_CLASSES = {
    "trailing_standardizer_v1": "TrailingStandardizer",
}

SLICE_3C_FITTING_EXPORTS = {
    "encode_representation",
    "fit_model",
    "fit_representation",
    "infer_model",
    "view_span_from_bounded_view",
}

SLICE_3C_FITTED_IDENTITY_FIELDS = {
    "dependencies",
    "features",
    "fit_isolation",
    "format",
    "format_version",
    "platform",
    "plugin",
    "representation",
    "split_policy",
    "tolerance",
    "training_outcome",
    "training_view",
}

SLICE_3D_LEAKAGE_TEST_PATH = Path(__file__).with_name("test_model_leakage.py")

SLICE_3D_NEGATIVE_CONTROL_CLASSES = frozenset(
    {
        "CarriedStateAccumulatorControl",
        "EvaluationRangeFitRepresentationControl",
        "ForwardShiftModelControl",
        "SelfForwardReadFitModelControl",
        "WholeEvaluationRangeNormalizerControl",
    }
)

SLICE_3D_COVERED_MODELS = {
    "gap_sensitive_gate_v1",
    "multi_confluence_gate_v1",
    "reference_library_v1",
    "stateless_rule_v1",
    "supervised_mean_v1",
    "uls_push_gate_v1",
    "zone_event_gate_v1",
}

SLICE_3D_COVERED_REPRESENTATIONS = {"trailing_standardizer_v1"}

PHASE3_EXPERIMENT_INVENTORY = {
    "__init__.py",
    "__main__.py",
    "cli.py",
    "config.py",
    "decisions.py",
    "evaluation.py",
    "features.py",
    "fitting.py",
    "folds.py",
    "orchestration.py",
    "outcomes.py",
    "pool.py",
    "resources.py",
    "run_identity.py",
    "runs.py",
    "sweeps.py",
    "training_bundles.py",
    "uls_entry_bundles.py",
    "uls_joint_bundles.py",
    "uls_tp_bundles.py",
    "uls_tp_oof.py",
    "worker.py",
}

SLICE_3I_PUBLIC_SURFACES = {
    "config.py": {"ResolvedExperimentConfig", "annotations", "load_resolved_config"},
    "orchestration.py": {"annotations", "run_experiment", "verify_experiment"},
    "cli.py": {"annotations", "main"},
    "worker.py": {"annotations"},
    "__main__.py": set(),
}

SLICE_3I_DECLARED_EXPORTS = {
    "config.py": {"ResolvedExperimentConfig", "load_resolved_config"},
    "orchestration.py": {"run_experiment", "verify_experiment"},
    "cli.py": {"main"},
    "worker.py": set(),
    "__main__.py": set(),
}

SLICE_3I_CLI_VERBS = {"list", "run", "status", "verify"}

SLICE_3J_QUALIFICATION_TEST_PATH = Path(__file__).with_name("test_phase3_qualification.py")
SLICE_3J_PERFORMANCE_TEST_PATH = Path(__file__).parent / "performance" / "test_phase3_run_budget.py"
SLICE_3J_USE_CASES = {
    "cache_bypassing_verification",
    "multi_policy_fanout",
    "representation_reference_library",
    "stateless_descriptive",
    "stateless_evaluated",
    "supervised_target_channel",
}

SLICE_3E_FOLD_TEST_PATH = Path(__file__).with_name("test_folds.py")

SLICE_3E_NEGATIVE_CONTROL_CLASSES = frozenset(
    {
        "OverbroadOverrideFoldControl",
        "PurgeDisabledFoldControl",
    }
)

SLICE_3F_DECISION_INVENTORY = {
    "__init__.py",
    "execution.py",
    "reference.py",
    "registry.py",
}

SLICE_3F_POLICY_CLASSES = {
    "adaptive_bracket_exposure_v1": "AdaptiveBracketExposurePolicy",
    "abstain_v1": "AbstainPolicy",
    "bracket_threshold_v1": "BracketThresholdPolicy",
    "event_exposure_v1": "EventExposurePolicy",
    "threshold_v1": "ThresholdPolicy",
    "uls_joint_bracket_exposure_v1": "UlsJointBracketExposurePolicy",
    "zone_adaptive_bracket_exposure_v1": "ZoneAdaptiveBracketExposurePolicy",
    "zone_fixed_bracket_exposure_v1": "ZoneFixedBracketExposurePolicy",
}

SLICE_3F_DECISION_TEST_PATH = Path(__file__).with_name("test_decisions.py")

SLICE_3G_EVALUATION_INVENTORY = {
    "__init__.py",
    "execution.py",
    "reference.py",
    "registry.py",
    "trade_reference.py",
}

SLICE_3G_EVALUATOR_CLASSES = {
    "rank_ic_v1": "RankIC",
    "sign_accuracy_v1": "SignAccuracy",
}

SLICE_4E_TRADE_EVALUATOR_CLASSES = {
    "equity_drawdown_v1": "EquityDrawdown",
    "gross_r_summary_v1": "GrossRSummary",
    "trade_summary_v1": "TradeSummary",
}

SLICE_3G_EVALUATION_TEST_PATH = Path(__file__).with_name("test_evaluation.py")

SLICE_3H_RUN_EXPORTS = {"RunManifest", "RunState", "scan_runs"}

SLICE_3H_RUN_STATES = {
    "PENDING",
    "RUNNING",
    "COMPLETE",
    "FAILED",
    "INTERRUPTED",
}

SLICE_3H_RUN_TEST_PATH = Path(__file__).with_name("test_runs.py")

SLICE_2A_CONTRACT_MODULES = (
    "core/features.py",
    "core/feature_frames.py",
)

SLICE_2B_MODULE_SURFACES = {
    "features/execution.py": (set(), {"annotations"}),
    "features/reference.py": (
        {
            "BarRange",
            "GapSensitiveLevelsH1V1",
            "LogReturn1",
            "MultiConfluenceM15V1",
            "RollingMeanClose",
            "UlsMarketContextV1",
            "UlsPushV7",
            "ZoneContextV1",
            "ZoneTraderV13",
        },
        {
            "BarRange",
            "GapSensitiveLevelsH1V1",
            "LogReturn1",
            "MultiConfluenceM15V1",
            "RollingMeanClose",
            "UlsMarketContextV1",
            "UlsPushV7",
            "ZoneContextV1",
            "ZoneTraderV13",
            "annotations",
        },
    ),
    "experiments/features.py": (
        {"compute_features", "materialize_features"},
        {"compute_features", "materialize_features", "annotations"},
    ),
}

SLICE_2B_PROVIDER_SURFACE = {
    "compute",
    "cost_hint",
    "feature_names",
    "params",
    "provider_id",
    "required_columns",
    "version",
    "window",
}

SLICE_2C_LEAKAGE_TEST_PATH = Path(__file__).with_name("test_feature_leakage.py")

SLICE_2C_NEGATIVE_CONTROL_CLASSES = frozenset(
    {
        "CenteredWindowProvider",
        "EvalEndPeekProvider",
        "FutureShiftProvider",
        "GapCrossingUnderBreakProvider",
        "UnderDeclaredReachProvider",
        "WholeDatasetNormalizationProvider",
    }
)

SLICE_2C_LIVE_FACTORY_PROVENANCE = {
    "bar_range": (f"{ROOT_NAME}.features.reference", "BarRange"),
    "gap_sensitive_levels_h1_v1": (
        f"{ROOT_NAME}.features.reference",
        "GapSensitiveLevelsH1V1",
    ),
    "log_return_1": (f"{ROOT_NAME}.features.reference", "LogReturn1"),
    "multi_confluence_m15_v1": (
        f"{ROOT_NAME}.features.reference",
        "MultiConfluenceM15V1",
    ),
    "rolling_mean_close": (f"{ROOT_NAME}.features.reference", "RollingMeanClose"),
    "uls_market_context_v1": (f"{ROOT_NAME}.features.reference", "UlsMarketContextV1"),
    "uls_push_v7": (f"{ROOT_NAME}.features.reference", "UlsPushV7"),
    "zone_context_v1": (f"{ROOT_NAME}.features.reference", "ZoneContextV1"),
    "zone_trader_v13": (f"{ROOT_NAME}.features.reference", "ZoneTraderV13"),
}

SLICE_2C_ALLOWED_LEAKAGE_IMPORT_PREFIXES = (
    "__future__",
    "collections",
    "dataclasses",
    "numpy",
    "pytest",
    "typing",
    f"{ROOT_NAME}.core",
    f"{ROOT_NAME}.features",
)

SLICE_2C_FORBIDDEN_AUTHORITY_IDENTIFIERS = {
    "AccessGrant",
    "BoundedView",
    "DatasetRegistry",
    "GatedUnlock",
    "MarketFrameLoader",
    "authorize_access",
    "open_bounded_view",
}

SLICE_2C_FORBIDDEN_AUTHORITY_CALLS = {
    "__import__",
    "compile",
    "eval",
    "exec",
    "fromfile",
    "genfromtxt",
    "getenv",
    "glob",
    "load",
    "loadtxt",
    "memmap",
    "open",
    "read_bytes",
    "read_text",
    "rglob",
    "save",
    "savetxt",
    "savez",
    "savez_compressed",
    "tofile",
    "write_bytes",
    "write_text",
}

SLICE_2C_FORBIDDEN_GATE_EVASION_REFERENCES = {
    "SkipTest",
    "allclose",
    "approx",
    "assertAlmostEqual",
    "assert_allclose",
    "assert_almost_equal",
    "assert_array_almost_equal",
    "importorskip",
    "isclose",
    "skip",
    "skipIf",
    "skipUnless",
    "skipif",
    "xfail",
}

SLICE_2C_FORBIDDEN_TOLERANCE_IDENTIFIERS = {
    "atol",
    "epsilon",
    "rtol",
    "tol",
    "tolerance",
}

SLICE_2F_MODULE_INVENTORY = {
    "__init__.py",
    "container.py",
    "identity.py",
    "materialization.py",
    "store.py",
}

SLICE_2G_MODULE_INVENTORY = {
    *SLICE_2F_MODULE_INVENTORY,
    "determinism.py",
    "fitted.py",
}

SLICE_2D_PUBLIC_EXPORTS = {
    "PlatformIdentity",
    "StageIdentity",
}

SLICE_2F_PUBLIC_EXPORTS = {
    "ArtifactStore",
    "MaterializationPolicy",
    *SLICE_2D_PUBLIC_EXPORTS,
}

SLICE_2G_DETERMINISM_EXPORTS = {
    "DeterminismReport",
    "verify_determinism",
}

SLICE_2G_PUBLIC_EXPORTS = {
    *SLICE_2F_PUBLIC_EXPORTS,
    *SLICE_2G_DETERMINISM_EXPORTS,
}

SLICE_2G_DETERMINISM_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "hashlib",
    "numpy",
    "re",
    f"{ROOT_NAME}.artifacts.container",
    f"{ROOT_NAME}.artifacts.identity",
    f"{ROOT_NAME}.artifacts.materialization",
    f"{ROOT_NAME}.core.errors",
    f"{ROOT_NAME}.core.feature_frames",
    f"{ROOT_NAME}.core.features",
    "typing",
}

SLICE_2G_FORBIDDEN_REFERENCES = {
    "AccessContext",
    "AccessGrant",
    "ArtifactStore",
    "BoundedView",
    "DatasetRegistry",
    "FeatureProvider",
    "FeatureRequest",
    "GatedUnlock",
    "MarketFrame",
    "MarketFrameLoader",
    "SplitPolicyIdentity",
    "ViewRole",
    "_materialize_feature_frame",
    "authorize_access",
    "compute_features",
    "materialize_features",
    "open_bounded_view",
    "read_manifest",
    "read_split_policy",
}

SLICE_2G_FORBIDDEN_IDENTIFIERS = {
    *SLICE_2C_FORBIDDEN_TOLERANCE_IDENTIFIERS,
    "equal_nan",
}

SLICE_2F_POLICY_MEMBERS = {
    "EPHEMERAL": "ephemeral",
    "CACHE_IF_EXPENSIVE": "cache_if_expensive",
    "PERSIST": "persist",
    "PERSIST_ON_FAILURE": "persist_on_failure",
}

SLICE_2F_MATERIALIZATION_IMPORTS = {
    "__future__",
    "collections.abc",
    "enum",
    "numpy",
    "numpy.typing",
    f"{ROOT_NAME}.artifacts.container",
    f"{ROOT_NAME}.artifacts.identity",
    f"{ROOT_NAME}.artifacts.store",
    f"{ROOT_NAME}.core.errors",
    f"{ROOT_NAME}.core.feature_frames",
    f"{ROOT_NAME}.core.features",
    f"{ROOT_NAME}.core.identity",
    f"{ROOT_NAME}.core.time",
    "typing",
}

SLICE_2F_FORBIDDEN_MATERIALIZATION_REFERENCES = {
    "AccessContext",
    "AccessGrant",
    "BoundedView",
    "DatasetRef",
    "DatasetRegistry",
    "FeatureProvider",
    "FeatureRequest",
    "GatedUnlock",
    "MarketFrame",
    "MarketFrameLoader",
    "SplitPolicyIdentity",
    "ViewRole",
    "authorize_access",
    "open_bounded_view",
    "read_manifest",
    "read_split_policy",
}

SLICE_2F_FORBIDDEN_POLICY_CALLS = {
    "Popen",
    "__import__",
    "breakpoint",
    "chdir",
    "compile",
    "cwd",
    "eval",
    "exec",
    "expanduser",
    "getcwd",
    "getenv",
    "gethostname",
    "getpid",
    "glob",
    "home",
    "input",
    "iterdir",
    "listdir",
    "locals",
    "lstat",
    "memmap",
    "mkdir",
    "monotonic",
    "open",
    "perf_counter",
    "read_bytes",
    "read_text",
    "rename",
    "replace",
    "resolve",
    "rglob",
    "run",
    "scandir",
    "sleep",
    "system",
    "time",
    "touch",
    "unlink",
    "walk",
    "write_bytes",
    "write_text",
}

SLICE_2G_FORBIDDEN_CALLS = {
    *SLICE_2F_FORBIDDEN_POLICY_CALLS,
    "Random",
    "RandomState",
    "allclose",
    "approx",
    "assert_allclose",
    "default_rng",
    "get",
    "hash",
    "isclose",
    "normal",
    "put",
    "rand",
    "randint",
    "randn",
    "random",
    "seed",
    "uniform",
}

SLICE_2F_TEST_PATH = Path(__file__).with_name("test_feature_materialization.py")

SLICE_2F_ALLOWED_TEST_IMPORTS = {
    "__future__",
    "collections",
    "collections.abc",
    "hashlib",
    "json",
    "numpy",
    "os",
    "pathlib",
    "pytest",
    "subprocess",
    "sys",
    f"{ROOT_NAME}.artifacts",
    f"{ROOT_NAME}.artifacts.container",
    f"{ROOT_NAME}.artifacts.materialization",
    f"{ROOT_NAME}.core",
    f"{ROOT_NAME}.core.feature_frames",
    f"{ROOT_NAME}.core.frames",
    f"{ROOT_NAME}.data",
    f"{ROOT_NAME}.data.views",
    f"{ROOT_NAME}.experiments.features",
    "typing",
}

SLICE_2G_TEST_PATH = Path(__file__).with_name("test_feature_determinism.py")

SLICE_2G_ALLOWED_TEST_IMPORTS = {
    "__future__",
    "collections",
    "collections.abc",
    "dataclasses",
    "hashlib",
    "json",
    "numpy",
    "numpy.typing",
    "os",
    "pathlib",
    "pytest",
    "subprocess",
    "sys",
    f"{ROOT_NAME}.artifacts",
    f"{ROOT_NAME}.artifacts.container",
    f"{ROOT_NAME}.artifacts.determinism",
    f"{ROOT_NAME}.core",
    f"{ROOT_NAME}.core.feature_frames",
    f"{ROOT_NAME}.core.frames",
    f"{ROOT_NAME}.data",
    f"{ROOT_NAME}.data.views",
    f"{ROOT_NAME}.experiments.features",
    "typing",
}

SLICE_2D_IDENTITY_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "hashlib",
    "json",
    "re",
    f"{ROOT_NAME}.core.errors",
    f"{ROOT_NAME}.core.features",
    f"{ROOT_NAME}.core.identity",
    f"{ROOT_NAME}.core.interfaces",
    f"{ROOT_NAME}.core.time",
    "typing",
}

SLICE_2D_FORBIDDEN_PURITY_CALLS = {
    "Popen",
    "__import__",
    "breakpoint",
    "check_call",
    "check_output",
    "compile",
    "cwd",
    "dir",
    "eval",
    "exec",
    "exists",
    "expanduser",
    "getcwd",
    "getenv",
    "gethostname",
    "getpid",
    "getppid",
    "glob",
    "globals",
    "hash",
    "home",
    "hostname",
    "id",
    "input",
    "is_dir",
    "is_file",
    "iterdir",
    "listdir",
    "locals",
    "lstat",
    "mkdir",
    "monotonic",
    "open",
    "perf_counter",
    "read",
    "read_bytes",
    "read_text",
    "rename",
    "repr",
    "resolve",
    "rglob",
    "run",
    "scandir",
    "sleep",
    "stat",
    "system",
    "time",
    "touch",
    "uname",
    "unlink",
    "vars",
    "walk",
    "write",
    "write_bytes",
    "write_text",
}

SLICE_2D_FORBIDDEN_HASH_CALLS = {
    "adler32",
    "blake2b",
    "blake2s",
    "crc32",
    "hash",
    "md5",
    "sha1",
    "sha224",
    "sha384",
    "sha512",
}

SLICE_2D_FORBIDDEN_AMBIENT_REFERENCES = {
    "AccessContext",
    "AccessGrant",
    "BoundedView",
    "DatasetRef",
    "Path",
    "PathLike",
    "SplitPolicyIdentity",
    "ViewRole",
    "__file__",
    "argv",
    "environ",
}

SLICE_2E_CONTAINER_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "hashlib",
    "io",
    "json",
    "numpy",
    "numpy.typing",
    "re",
    f"{ROOT_NAME}.artifacts.identity",
    f"{ROOT_NAME}.core.errors",
    f"{ROOT_NAME}.core.feature_frames",
    f"{ROOT_NAME}.core.features",
    f"{ROOT_NAME}.core.identity",
    f"{ROOT_NAME}.core.time",
    "types",
    "typing",
    "zipfile",
}

SLICE_2E_STORE_IMPORTS = {
    "__future__",
    "fcntl",
    "hashlib",
    "os",
    "pathlib",
    "re",
    "stat",
    f"{ROOT_NAME}.artifacts.container",
    f"{ROOT_NAME}.core.errors",
    "typing",
}

SLICE_2E_FORBIDDEN_ARTIFACT_REFERENCES = {
    "AccessContext",
    "AccessGrant",
    "Bar",
    "BoundedView",
    "DatasetRef",
    "DatasetRegistry",
    "DecisionPolicy",
    "Direction",
    "ExperimentConfig",
    "ExperimentResult",
    "FeatureRequest",
    "GatedUnlock",
    "MarketContext",
    "MarketFrame",
    "MarketFrameLoader",
    "ModelOutput",
    "OutcomeSpec",
    "PipelineRecord",
    "ResearchInput",
    "ResearchModel",
    "RunManifest",
    "TrainingTargetFrame",
    "ViewRole",
    "authorize_access",
    "open_bounded_view",
    "read_manifest",
    "read_split_policy",
}

SLICE_2E_FORBIDDEN_CONTAINER_CALLS = {
    "FileIO",
    "Popen",
    "chdir",
    "cwd",
    "expanduser",
    "fromfile",
    "fromregex",
    "genfromtxt",
    "getcwd",
    "getenv",
    "glob",
    "home",
    "iterdir",
    "listdir",
    "loadtxt",
    "lstat",
    "memmap",
    "mkdir",
    "open",
    "read_bytes",
    "read_text",
    "rename",
    "replace",
    "resolve",
    "rglob",
    "run",
    "save",
    "savetxt",
    "savez_compressed",
    "scandir",
    "system",
    "tofile",
    "unlink",
    "walk",
    "write_bytes",
    "write_text",
}

SLICE_2E_FORBIDDEN_STORE_CALLS = {
    "Popen",
    "chdir",
    "cwd",
    "expanduser",
    "getcwd",
    "getenv",
    "glob",
    "home",
    "iterdir",
    "read_bytes",
    "read_text",
    "rename",
    "replace",
    "resolve",
    "rglob",
    "run",
    "scandir",
    "system",
    "walk",
    "write_bytes",
    "write_text",
}

SLICE_2E_FORBIDDEN_DYNAMIC_CALLS = {
    "__import__",
    "compile",
    "delattr",
    "eval",
    "exec",
    "globals",
    "hasattr",
    "locals",
    "setattr",
    "vars",
}

SLICE_2E_STORE_OS_CAPABILITIES = {
    "fcntl.flock",
    "os.close",
    "os.fstat",
    "os.fsync",
    "os.link",
    "os.listdir",
    "os.mkdir",
    "os.open",
    "os.read",
    "os.unlink",
    "os.urandom",
    "os.write",
    "stat.S_ISREG",
}

SLICE_2E_ATOMIC_WRITE_SHA256 = "85e0691f8b1b5da18c6bb3708bd2bf64bb6f223111c2fa772445af92656ec763"

SLICE_2E_TEST_MODULES = (
    "test_artifact_container.py",
    "test_artifact_store.py",
)

SLICE_2E_ALLOWED_TEST_IMPORT_PREFIXES = (
    "__future__",
    "base64",
    "collections",
    "contextlib",
    "dataclasses",
    "fcntl",
    "hashlib",
    "io",
    "json",
    "numpy",
    "os",
    "pathlib",
    "pytest",
    "stat",
    "subprocess",
    "sys",
    "threading",
    "time",
    "typing",
    "warnings",
    "zipfile",
    f"{ROOT_NAME}.artifacts",
    f"{ROOT_NAME}.core",
)

GAP_INDEX_PUBLIC_SURFACE = frozenset(
    {
        "clock_domain",
        "dataset_ref",
        "delta",
        "deviation",
        "from_frame",
        "next_key",
        "nominal_interval",
        "num_gaps",
        "prev_key",
        "unit",
    }
)

SLICE_1D_COMPUTATIONAL_MODULES = (
    "core/frames.py",
    "data/gaps.py",
    "data/integrity.py",
    # Slice 1E composition shares the same native-time and legacy-carrier
    # prohibitions as the Slice 1D row-loading boundary it orchestrates.
    "data/views.py",
)

CORE_FRAME_PUBLIC_BINDINGS = {
    "MarketFrame": {f"{ROOT_NAME}.core.frames.MarketFrame"},
}

CORE_FRAME_MODULE_PUBLIC_BINDINGS = {
    "Any",
    "ClockDomain",
    "ClockDomainError",
    "DatasetRef",
    "Mapping",
    "MappingProxyType",
    "MarketFrame",
    "MarketFrameError",
    "NativeTimeKey",
    "Sequence",
    "TYPE_CHECKING",
    "TimeUnit",
    "TimeUnitMismatchError",
    "annotations",
    "cast",
    "dataclass",
    "np",
}

_LOCAL_CLASS = "<module-owned class>"
_LOCAL_FUNCTION = "<module-owned function>"

SLICE_2D_IDENTITY_SOURCE_BINDINGS = {
    "PlatformIdentity": _LOCAL_CLASS,
    "StageIdentity": _LOCAL_CLASS,
    "annotations": "__future__.annotations",
}

SLICE_2D_IDENTITY_RUNTIME_BINDINGS = {
    "PlatformIdentity": f"{ROOT_NAME}.artifacts.identity.PlatformIdentity",
    "StageIdentity": f"{ROOT_NAME}.artifacts.identity.StageIdentity",
    "annotations": "__future__.annotations",
}

SLICE_2F_PACKAGE_SOURCE_BINDINGS = {
    "ArtifactStore": f"{ROOT_NAME}.artifacts.store.ArtifactStore",
    "MaterializationPolicy": (f"{ROOT_NAME}.artifacts.materialization.MaterializationPolicy"),
    **{name: f"{ROOT_NAME}.artifacts.identity.{name}" for name in SLICE_2D_PUBLIC_EXPORTS},
}

SLICE_2F_PACKAGE_RUNTIME_BINDINGS = {
    **SLICE_2F_PACKAGE_SOURCE_BINDINGS,
    "container": f"{ROOT_NAME}.artifacts.container",
    "identity": f"{ROOT_NAME}.artifacts.identity",
    "materialization": f"{ROOT_NAME}.artifacts.materialization",
    "store": f"{ROOT_NAME}.artifacts.store",
}

SLICE_2G_PACKAGE_SOURCE_BINDINGS = {
    **SLICE_2F_PACKAGE_SOURCE_BINDINGS,
    "DeterminismReport": f"{ROOT_NAME}.artifacts.determinism.DeterminismReport",
    "verify_determinism": f"{ROOT_NAME}.artifacts.determinism.verify_determinism",
}

SLICE_2G_PACKAGE_RUNTIME_BINDINGS = {
    **SLICE_2G_PACKAGE_SOURCE_BINDINGS,
    "container": f"{ROOT_NAME}.artifacts.container",
    "determinism": f"{ROOT_NAME}.artifacts.determinism",
    "fitted": f"{ROOT_NAME}.artifacts.fitted",
    "identity": f"{ROOT_NAME}.artifacts.identity",
    "materialization": f"{ROOT_NAME}.artifacts.materialization",
    "store": f"{ROOT_NAME}.artifacts.store",
}

SLICE_2G_DETERMINISM_SOURCE_BINDINGS = {
    "DeterminismReport": _LOCAL_CLASS,
    "annotations": "__future__.annotations",
    "verify_determinism": _LOCAL_FUNCTION,
}

SLICE_2G_DETERMINISM_RUNTIME_BINDINGS = {
    "DeterminismReport": f"{ROOT_NAME}.artifacts.determinism.DeterminismReport",
    "annotations": "__future__.annotations",
    "verify_determinism": f"{ROOT_NAME}.artifacts.determinism.verify_determinism",
}

SLICE_2F_MATERIALIZATION_SOURCE_BINDINGS = {
    "MaterializationPolicy": _LOCAL_CLASS,
    "annotations": "__future__.annotations",
}

SLICE_2F_MATERIALIZATION_RUNTIME_BINDINGS = {
    "MaterializationPolicy": (f"{ROOT_NAME}.artifacts.materialization.MaterializationPolicy"),
    "annotations": "__future__.annotations",
}

SLICE_2F_EXPERIMENT_SOURCE_BINDINGS = {
    "annotations": "__future__.annotations",
    "compute_features": _LOCAL_FUNCTION,
    "materialize_features": _LOCAL_FUNCTION,
}

SLICE_2F_EXPERIMENT_RUNTIME_BINDINGS = {
    "annotations": "__future__.annotations",
    "compute_features": f"{ROOT_NAME}.experiments.features.compute_features",
    "materialize_features": (f"{ROOT_NAME}.experiments.features.materialize_features"),
}

SLICE_2E_STORE_SOURCE_BINDINGS = {
    "ArtifactStore": _LOCAL_CLASS,
    "annotations": "__future__.annotations",
}

SLICE_2E_STORE_RUNTIME_BINDINGS = {
    "ArtifactStore": f"{ROOT_NAME}.artifacts.store.ArtifactStore",
    "annotations": "__future__.annotations",
}

CORE_FRAME_CANONICAL_BINDINGS = {
    "Any": "typing.Any",
    "ClockDomain": f"{ROOT_NAME}.core.time.ClockDomain",
    "ClockDomainError": f"{ROOT_NAME}.core.errors.ClockDomainError",
    "DatasetRef": f"{ROOT_NAME}.core.identity.DatasetRef",
    "Mapping": "collections.abc.Mapping",
    "MappingProxyType": "types.MappingProxyType",
    "MarketFrame": _LOCAL_CLASS,
    "MarketFrameError": f"{ROOT_NAME}.core.errors.MarketFrameError",
    "NativeTimeKey": f"{ROOT_NAME}.core.time.NativeTimeKey",
    "Sequence": "collections.abc.Sequence",
    "TYPE_CHECKING": "typing.TYPE_CHECKING",
    "TimeUnit": f"{ROOT_NAME}.core.time.TimeUnit",
    "TimeUnitMismatchError": f"{ROOT_NAME}.core.errors.TimeUnitMismatchError",
    "annotations": "__future__.annotations",
    "cast": "typing.cast",
    "dataclass": "dataclasses.dataclass",
    "np": "numpy",
}

CORE_FRAME_RUNTIME_BINDINGS = {
    **CORE_FRAME_CANONICAL_BINDINGS,
    "MarketFrame": f"{ROOT_NAME}.core.frames.MarketFrame",
}

FEATURES_PUBLIC_EXPORTS = {
    "CostHint",
    "FeatureOrigin",
    "FeatureRequest",
    "GapPolicy",
    "ParamValue",
    "WindowSpec",
    "canonical_params_json",
}

FEATURES_CANONICAL_BINDINGS = {
    "CostHint": _LOCAL_CLASS,
    "FeatureOrigin": _LOCAL_CLASS,
    "FeatureRequest": _LOCAL_CLASS,
    "GapPolicy": _LOCAL_CLASS,
    "WindowSpec": _LOCAL_CLASS,
    "annotations": "__future__.annotations",
    "canonical_params_json": _LOCAL_FUNCTION,
}

PARAM_VALUE_CANONICAL_REFERENCES = {
    "ParamValue",
    "bool",
    "float",
    "int",
    "str",
    "tuple",
}

FEATURES_RUNTIME_BINDINGS = {
    **{name: f"{ROOT_NAME}.core.features.{name}" for name in FEATURES_PUBLIC_EXPORTS},
    "annotations": "__future__.annotations",
}

FEATURE_FRAMES_CANONICAL_BINDINGS = {
    "FeatureFrame": _LOCAL_CLASS,
    "annotations": "__future__.annotations",
}

FEATURE_FRAMES_RUNTIME_BINDINGS = {
    "FeatureFrame": f"{ROOT_NAME}.core.feature_frames.FeatureFrame",
    "annotations": "__future__.annotations",
}

CORE_PACKAGE_CANONICAL_BINDINGS = {
    **{
        name: f"{ROOT_NAME}.core.errors.{name}"
        for name in (
            "AccessDeniedError",
            "AccessGrantMismatchError",
            "AuditCorruptionError",
            "AuditError",
            "AuditLockError",
            "AuditOpenError",
            "AuditSyncError",
            "AuditWriteError",
            "ClockDomainError",
            "ContractViolationError",
            "DatasetNotFoundError",
            "DatasetRefMismatchError",
            "DatasetRegistryError",
            "DuplicateComponentError",
            "DuplicateDatasetIdError",
            "FeatureContractError",
            "FeatureFrameError",
            "FeatureRequestError",
            "IntegrityError",
            "InsufficientContextError",
            "InvalidAccessReasonError",
            "MalformedManifestError",
            "MalformedSplitPolicyError",
            "ManifestError",
            "ManifestLocationError",
            "MarketFrameError",
            "ParentResolutionError",
            "PartitionReadError",
            "RegistryError",
            "RegistryRootError",
            "SplitPolicyError",
            "SplitPolicyMismatchError",
            "SplitPolicyNotFoundError",
            "TimeUnitMismatchError",
            "TradingResearchError",
            "UnknownComponentError",
            "UnsupportedManifestFormatError",
            "UnsupportedMarketFrameRoleError",
            "UnsupportedSplitPolicyError",
            "ViewBoundsError",
            "ViewCompositionError",
            "ViewMemberError",
        )
    },
    "FeatureFrame": f"{ROOT_NAME}.core.feature_frames.FeatureFrame",
    **{
        name: f"{ROOT_NAME}.core.features.{name}"
        for name in (
            "CostHint",
            "FeatureOrigin",
            "FeatureRequest",
            "GapPolicy",
            "ParamValue",
            "WindowSpec",
            "canonical_params_json",
        )
    },
    "MarketFrame": f"{ROOT_NAME}.core.frames.MarketFrame",
    **{
        name: f"{ROOT_NAME}.core.identity.{name}"
        for name in ("DatasetIdentity", "DatasetRef", "ViewRole")
    },
    **{
        name: f"{ROOT_NAME}.core.interfaces.{name}"
        for name in (
            "CorpusResearchModel",
            "DecisionPolicy",
            "Evaluator",
            "FeatureProvider",
            "OutcomeSpec",
            "RepresentationModel",
            "ResearchModel",
            "TradeEvaluator",
        )
    },
    **{
        name: f"{ROOT_NAME}.core.research.{name}"
        for name in (
            "BoundaryPolicy",
            "CorpusModelInputs",
            "MetricValue",
            "ModelInputs",
            "OutcomeRequest",
            "StageOrigin",
            "ToleranceSpec",
            "ViewSpan",
        )
    },
    **{
        name: f"{ROOT_NAME}.core.research_frames.{name}"
        for name in (
            "FittedPayload",
            "FittedState",
            "IntentFrame",
            "ModelOutputFrame",
            "OutcomeFrame",
            "RepresentationFrame",
            "TrainingTargetFrame",
        )
    },
    **{name: f"{ROOT_NAME}.core.simulation.{name}" for name in PHASE4_SIMULATION_EXPORTS},
    "Registry": f"{ROOT_NAME}.core.registry.Registry",
    **{
        name: f"{ROOT_NAME}.core.time.{name}"
        for name in ("CalendarTimePolicy", "ClockDomain", "NativeTimeKey", "TimeUnit")
    },
}

CORE_PACKAGE_RUNTIME_BINDINGS = {
    **CORE_PACKAGE_CANONICAL_BINDINGS,
    **{
        name: f"{ROOT_NAME}.core.{name}"
        for name in (
            "errors",
            "feature_frames",
            "features",
            "frames",
            "identity",
            "interfaces",
            "pipeline",
            "research",
            "research_frames",
            "registry",
            "simulation",
            "time",
        )
    },
}

INTEGRITY_MODULE_PUBLIC_BINDINGS = {
    "AccessGrant",
    "ClockDomainError",
    "ColumnPurpose",
    "DatasetRef",
    "DatasetRegistry",
    "ExitStack",
    "IntegrityError",
    "MarketFrame",
    "MarketFrameError",
    "MarketFrameLoader",
    "NativeTimeKey",
    "PartitionMetadata",
    "PartitionReadError",
    "SplitPolicy",
    "TYPE_CHECKING",
    "TimeUnitMismatchError",
    "UnsupportedMarketFrameRoleError",
    "ViewRole",
    "annotations",
    "dataclass",
    "hashlib",
    "np",
    "npt",
    "os",
    "stat",
}

INTEGRITY_CANONICAL_BINDINGS = {
    "AccessGrant": f"{ROOT_NAME}.data.gating.AccessGrant",
    "ClockDomainError": f"{ROOT_NAME}.core.errors.ClockDomainError",
    "ColumnPurpose": f"{ROOT_NAME}.data.manifests.ColumnPurpose",
    "DatasetRef": f"{ROOT_NAME}.core.identity.DatasetRef",
    "DatasetRegistry": f"{ROOT_NAME}.data.registry.DatasetRegistry",
    "ExitStack": "contextlib.ExitStack",
    "IntegrityError": f"{ROOT_NAME}.core.errors.IntegrityError",
    "MarketFrame": f"{ROOT_NAME}.core.frames.MarketFrame",
    "MarketFrameError": f"{ROOT_NAME}.core.errors.MarketFrameError",
    "MarketFrameLoader": _LOCAL_CLASS,
    "NativeTimeKey": f"{ROOT_NAME}.core.time.NativeTimeKey",
    "PartitionMetadata": f"{ROOT_NAME}.data.manifests.PartitionMetadata",
    "PartitionReadError": f"{ROOT_NAME}.core.errors.PartitionReadError",
    "SplitPolicy": f"{ROOT_NAME}.data.gating.SplitPolicy",
    "TYPE_CHECKING": "typing.TYPE_CHECKING",
    "TimeUnitMismatchError": f"{ROOT_NAME}.core.errors.TimeUnitMismatchError",
    "UnsupportedMarketFrameRoleError": (f"{ROOT_NAME}.core.errors.UnsupportedMarketFrameRoleError"),
    "ViewRole": f"{ROOT_NAME}.core.identity.ViewRole",
    "annotations": "__future__.annotations",
    "dataclass": "dataclasses.dataclass",
    "hashlib": "hashlib",
    "np": "numpy",
    "npt": "numpy.typing",
    "os": "os",
    "stat": "stat",
}

INTEGRITY_RUNTIME_BINDINGS = {
    **INTEGRITY_CANONICAL_BINDINGS,
    "MarketFrameLoader": f"{ROOT_NAME}.data.integrity.MarketFrameLoader",
}

VIEWS_PUBLIC_CONTRACTS = {
    "BoundedSegment",
    "BoundedView",
    "ContextResolution",
    "ContextShortfall",
    "ViewUnion",
    "ViewUnionMember",
    "open_bounded_view",
}

VIEWS_CANONICAL_BINDINGS = {
    "BoundedSegment": _LOCAL_CLASS,
    "BoundedView": _LOCAL_CLASS,
    "ContextResolution": _LOCAL_CLASS,
    "ContextShortfall": _LOCAL_CLASS,
    "ViewUnion": _LOCAL_CLASS,
    "ViewUnionMember": _LOCAL_CLASS,
    "annotations": "__future__.annotations",
    "open_bounded_view": _LOCAL_FUNCTION,
}

VIEWS_RUNTIME_BINDINGS = {
    **{name: f"{ROOT_NAME}.data.views.{name}" for name in VIEWS_PUBLIC_CONTRACTS},
    "annotations": "__future__.annotations",
}

GAPS_CANONICAL_BINDINGS = {
    "GapIndex": _LOCAL_CLASS,
    "annotations": "__future__.annotations",
}

GAPS_RUNTIME_BINDINGS = {
    "GapIndex": f"{ROOT_NAME}.data.gaps.GapIndex",
    "annotations": "__future__.annotations",
}

GAPS_ALLOWED_IMPORT_MODULES = {
    "__future__",
    "dataclasses",
    "numpy",
    "numpy.typing",
    "trading_research.core.errors",
    "trading_research.core.frames",
    "trading_research.core.identity",
    "trading_research.core.time",
    "typing",
}

GAPS_ALLOWED_CORE_IMPORT_NAMES = {
    "trading_research.core.errors": {"ContractViolationError"},
    "trading_research.core.frames": {"MarketFrame"},
    "trading_research.core.identity": {"DatasetRef"},
    "trading_research.core.time": {"ClockDomain", "TimeUnit"},
}

GAPS_TYPE_CHECKING_ONLY_IMPORTS = {
    "numpy.typing",
    "trading_research.core.identity",
    "trading_research.core.time",
}

GAPS_FORBIDDEN_SEMANTIC_TERMS = {
    "closure",
    "holiday",
    "intraweek",
    "missing",
    "normal_boundary",
    "outage",
    "rollover",
    "session",
    "weekend",
}

GAPS_FORBIDDEN_AUTHORITY_IDENTIFIERS = {
    "accesscontext",
    "accessgrant",
    "accessreason",
    "accessrequest",
    "adapter",
    "audit",
    "authorization",
    "authorize_access",
    "cache",
    "classify_access",
    "config",
    "datasetregistry",
    "discover",
    "environment",
    "filesystem",
    "gatedunlock",
    "gating",
    "jsonlauditwriter",
    "loader",
    "marketframeloader",
    "memo",
    "parquet",
    "path",
    "persist",
    "pyarrow",
    "read_split_policy",
    "registeredmanifest",
    "registry",
    "splitpolicy",
    "view_generation",
    "viewgeneration",
}

GAPS_FORBIDDEN_CAPABILITY_CALLS = {
    "dump",
    "fromfile",
    "getcwd",
    "getenv",
    "glob",
    "load",
    "memmap",
    "open",
    "read_bytes",
    "read_table",
    "read_text",
    "rglob",
    "save",
    "savetxt",
    "savez",
    "savez_compressed",
    "tofile",
    "write_bytes",
    "write_text",
}

DATA_PACKAGE_PUBLIC_EXPORTS = {
    "AccessClassification",
    "AccessContext",
    "AccessGrant",
    "AccessReason",
    "AccessRequest",
    "BoundedSegment",
    "BoundedView",
    "ContextResolution",
    "ContextShortfall",
    "DatasetRegistry",
    "GapIndex",
    "GatedUnlock",
    "JsonlAuditWriter",
    "MarketFrameLoader",
    "PartitionSourceLocation",
    "RegisteredManifest",
    "SplitPolicy",
    "SplitPolicyIdentity",
    "ViewUnion",
    "ViewUnionMember",
    "authorize_access",
    "classify_access",
    "open_bounded_view",
    "read_split_policy",
}

DATA_PACKAGE_RUNTIME_SUPPORT_BINDINGS = {
    "adapters",
    "audit",
    "gaps",
    "gating",
    "integrity",
    "manifests",
    "registry",
    "views",
}

# Importing an intentionally private submodule still installs that submodule on
# its parent package at runtime.  These names are not imported or exported by
# ``data/__init__.py``; they are permitted only when another test has already
# imported the exact internal module during collection.
DATA_PACKAGE_OPTIONAL_INTERNAL_BINDINGS = {
    "atomic_write",
    "views_generation",
}

DATA_PACKAGE_CANONICAL_BINDINGS = {
    "JsonlAuditWriter": f"{ROOT_NAME}.data.audit.JsonlAuditWriter",
    "GapIndex": f"{ROOT_NAME}.data.gaps.GapIndex",
    **{
        name: f"{ROOT_NAME}.data.gating.{name}"
        for name in (
            "AccessClassification",
            "AccessContext",
            "AccessGrant",
            "AccessReason",
            "AccessRequest",
            "GatedUnlock",
            "SplitPolicy",
            "SplitPolicyIdentity",
            "authorize_access",
            "classify_access",
            "read_split_policy",
        )
    },
    "MarketFrameLoader": f"{ROOT_NAME}.data.integrity.MarketFrameLoader",
    **{
        name: f"{ROOT_NAME}.data.registry.{name}"
        for name in (
            "DatasetRegistry",
            "PartitionSourceLocation",
            "RegisteredManifest",
        )
    },
    **{
        name: f"{ROOT_NAME}.data.views.{name}"
        for name in (
            "BoundedSegment",
            "BoundedView",
            "ContextResolution",
            "ContextShortfall",
            "ViewUnion",
            "ViewUnionMember",
            "open_bounded_view",
        )
    },
}

DATA_PACKAGE_RUNTIME_BINDINGS = {
    **DATA_PACKAGE_CANONICAL_BINDINGS,
    **{name: f"{ROOT_NAME}.data.{name}" for name in DATA_PACKAGE_RUNTIME_SUPPORT_BINDINGS},
}

RETIRED_PROVIDER_IDENTIFIERS = {
    "MARKET_DATA_PROVIDERS",
    "MarketDataProvider",
}
RETIRED_RESEARCH_SKELETONS = {
    "ComponentSpec",
    "ExperimentConfig",
    "ExperimentResult",
}
RETIRED_RESEARCH_CARRIERS = {
    "Bar",
    "Direction",
    "FeatureSet",
    "MarketContext",
    "ModelOutput",
    "PipelineRecord",
    "Representation",
    "ResearchInput",
    "Signal",
}
LEGACY_ROW_CARRIERS = {
    "MarketDataProvider",
    *RETIRED_RESEARCH_SKELETONS,
    *RETIRED_RESEARCH_CARRIERS,
}
LEGACY_CARRIER_MODULES = {
    f"{ROOT_NAME}.core.interfaces",
}

FORBIDDEN_MARKET_TIME_CALLS = {
    "astimezone",
    "datetime64",
    "fromisoformat",
    "fromtimestamp",
    "strptime",
    "timedelta64",
    "utcfromtimestamp",
}

NUMPY_CIVIL_DTYPE_NAMES = {"datetime64", "timedelta64"}
NUMPY_CIVIL_DTYPE_REFERENCE_NAMES = {
    *NUMPY_CIVIL_DTYPE_NAMES,
    "M8",
    "m8",
}
NUMPY_DTYPE_CONVERSION_CALLS = {
    "arange",
    "array",
    "asanyarray",
    "asarray",
    "astype",
    "dtype",
    "empty",
    "frombuffer",
    "fromiter",
    "full",
    "ones",
    "view",
    "zeros",
}

ADAPTER_RUNTIME_PUBLIC_ALLOWLIST = {
    "data/adapters/__init__.py": {"mt5"},
    "data/adapters/mt5.py": {"annotations"},
}

ADAPTER_CANONICAL_SOURCE_BINDINGS = {
    "data/adapters/__init__.py": {},
    "data/adapters/mt5.py": {"annotations": "__future__.annotations"},
}

ADAPTER_CANONICAL_RUNTIME_BINDINGS = {
    "data/adapters/__init__.py": {"mt5": f"{ROOT_NAME}.data.adapters.mt5"},
    "data/adapters/mt5.py": {"annotations": "__future__.annotations"},
}

GOVERNED_DYNAMIC_ATTRIBUTE_MODULES = (
    "core/__init__.py",
    "core/feature_frames.py",
    "core/features.py",
    "core/frames.py",
    "core/simulation.py",
    "data/__init__.py",
    "data/gaps.py",
    "data/integrity.py",
    "data/views.py",
    "experiments/__init__.py",
    "experiments/features.py",
    "artifacts/__init__.py",
    "artifacts/container.py",
    "artifacts/determinism.py",
    "artifacts/identity.py",
    "artifacts/materialization.py",
    "artifacts/store.py",
    "features/__init__.py",
    "features/execution.py",
    "features/reference.py",
    "features/registry.py",
)

APPROVED_PUBLIC_DECODER_ROUTE = ("data/integrity.py", "MarketFrameLoader.load")
APPROVED_PUBLIC_COMPOSITION_ROUTE = ("data/views.py", "open_bounded_view")
APPROVED_BOUNDED_FRAME_ACCESSORS = {
    ("data/views.py", "BoundedSegment.frame"),
    ("data/views.py", "BoundedView.eval_frame"),
}
APPROVED_LOADER_EXPORT = (
    "data/__init__.py",
    "MarketFrameLoader",
    "trading_research.data.integrity.MarketFrameLoader",
)

ROW_ROUTE_ACTIONS = {"decode", "fetch", "load", "materialize", "read", "scan"}
ROW_ROUTE_SUBJECTS = {
    "arrow",
    "data",
    "dataset",
    "frame",
    "market_frame",
    "parquet",
    "partition",
    "row",
    "table",
}

ROW_MACHINERY_NAMES = {
    "MarketFrameLoader",
    "ParquetFile",
    "_DecodedPartition",
    "_decode_partition",
    "_make_market_frame",
}

FORBIDDEN_LOADER_PARAMETER_TYPES = {
    "DatasetIdentity",
    "DatasetRef",
    "Path",
    "PathLike",
    "PurePath",
    "RegisteredManifest",
}

FORBIDDEN_COMPOSITION_PARAMETER_TYPES = {
    *FORBIDDEN_LOADER_PARAMETER_TYPES,
    "DatasetRegistry",
    "NormalizedManifest",
    "SplitPolicy",
}

FORBIDDEN_LOADER_OPERATIONS = {
    "AccessContext",
    "AccessReason",
    "AccessRequest",
    "GatedUnlock",
    "JsonlAuditWriter",
    "authorize_access",
    "classify_access",
    "read_manifest",
}

SLICE_1F_GENERATION_MODULES = (
    "data/atomic_write.py",
    "data/manifests/adapter_columns.py",
    "data/views_generation.py",
)

SLICE_1F_ALLOWED_INTERNAL_IMPORTS = {
    f"{ROOT_NAME}.core.identity",
    f"{ROOT_NAME}.data.atomic_write",
    f"{ROOT_NAME}.data.audit",
    f"{ROOT_NAME}.data.gating",
    f"{ROOT_NAME}.data.integrity",
    f"{ROOT_NAME}.data.manifests",
    f"{ROOT_NAME}.data.manifests.adapter_columns",
    f"{ROOT_NAME}.data.registry",
}


def _slice_1f_v1_policy_literals() -> set[int]:
    """Read the one permitted literal declaration from its conformance test."""
    path = Path(__file__).with_name("test_split_policy_conformance.py")
    tree = ast.parse(path.read_text(), filename=str(path))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_EXPECTED_V1_RANGES"
    )
    assert isinstance(assignment.value, ast.Dict)
    return {
        boundary.value
        for span in assignment.value.values
        if isinstance(span, ast.Tuple)
        for boundary in span.elts
        if isinstance(boundary, ast.Constant) and type(boundary.value) is int
    }


SLICE_1F_V1_POLICY_LITERALS = _slice_1f_v1_policy_literals()

SLICE_1F_FORBIDDEN_DECODER_LEAVES = {
    "ParquetFile",
    "_DecodedPartition",
    "_decode_partition",
    "_make_market_frame",
    "fromfile",
    "memmap",
    "read_parquet",
    "read_table",
}

SLICE_1F_FORBIDDEN_AMBIENT_LEAVES = {
    "DatasetRegistry",
    "MarketFrameLoader",
    "SplitPolicy",
    "chdir",
    "cwd",
    "discover",
    "environ",
    "expanduser",
    "getcwd",
    "getenv",
    "home",
    "read_split_policy",
}

SLICE_1F_DIRECT_PUBLICATION_LEAVES = {
    "link",
    "open",
    "rename",
    "replace",
    "symlink",
    "touch",
    "write",
    "write_bytes",
    "write_text",
}

SLICE_1F_MANIFEST_READ_CALLS = {
    "_publish_fresh": {"snapshot_path.read_bytes"},
    "_publish_resume": {"anchor.path.read_bytes"},
    "_publish_children": {"path.read_bytes"},
    "_read_and_validate_anchor": {"path.read_bytes"},
}

PERFORMANCE_MEASURE_CHILD = Path(__file__).parent / "performance" / "_measure_child.py"
SNAPSHOT_OPERATOR_MODULE = Path(__file__).parent / "performance" / "_snapshot_operator.py"
FEATURE_BUDGET_TEST = Path(__file__).parent / "performance" / "test_feature_budgets.py"

SLICE_2H_PERFORMANCE_INVENTORY = {
    "__init__.py",
    "_measure_child.py",
    "_snapshot_operator.py",
    "test_feature_budgets.py",
    "test_load_budgets.py",
    "test_phase3_run_budget.py",
    "test_phase4_simulation_budget.py",
    "test_phase5_sweep_budget.py",
    "test_phase6_zone_trader_acceptance.py",
}
SLICE_2H_EVIDENCE_KEYS = {
    "artifact_bytes",
    "artifact_relative_path",
    "audit_record_count_after",
    "audit_record_count_before",
    "audit_sha256_after",
    "audit_sha256_before",
    "cache_hit_budget_seconds",
    "canonical_persist_status",
    "content_hash",
    "cross_nan_counts",
    "break_nan_counts",
    "full_pass_budget_seconds",
    "git_commit",
    "git_diff_sha256",
    "git_dirty",
    "gap_index_num_gaps",
    "identity_key",
    "peak_rss_bytes",
    "pre_measure_rss_bytes",
    "provider_order",
    "row_count",
    "runs_root",
    "schema",
    "target_dataset_id",
    "target_manifest_sha256",
    "target_role",
}
SLICE_2H_TIMING_PREFIXES = {
    "break_pass",
    "cache_hit",
    "full_pass",
    "full_plus_persist",
    "gap_index",
    "load_plus_full",
}
SLICE_2H_FUNCTION_AST_SHA256 = {
    "_audit_snapshot": "de39e9f713459cc753376ea329481dafd242c1a11aca7b90a31767d86c5a82ca",
    "_break_reference_providers": (
        "950232fe1acaf2ab0a4c7a72b7f8818951ad7f5160c139dab465ba3e5fdc529d"
    ),
    "_clean_committed_platform_identity": (
        "ed196c16a65e816adff6c241d7b444a091af04e8bb72f633b7dbc3935b327f2d"
    ),
    "_ensure_canonical_cache_entry": (
        "fe80d15cef5ebb85fc0f0f627195216a0cc7757eac0e4d039c7b8a925e355621"
    ),
    "_expected_break_nan_counts": (
        "787b9a140863e906c764696a7cdfa4f889d30dce5ecd29ea315695bd4f79f457"
    ),
    "_materialize": "d8966e4c7ca79f35a6c8601b7d4052024b9d4f809adf323df845d97ec23058f0",
    "_measure_cross_passes": ("5079247595cb2e1ab72e2454165468a8d1f49ef03a21b7e5ae3f25647e16ccaa"),
    "_measure_feature_passes": ("1284625d800fe51f03ef703a213c1be63ebacb089828cc73c853b81a76eed5ed"),
    "_measure_full_plus_persist": (
        "069b15e44b74ad32d509bdc4b2dbe7ec22547f5a87a23b2a650d70c9733031d4"
    ),
    "_measure_gap_index": ("c79935a2b1b467cb71037ae461d63cf33f37bc447365432eec88cb40cb24e837"),
    "_measure_load_plus_full": ("118dbc5d7cb8001803a89ee9c5a320e41ef77c28dcead8cf10c1277598fab214"),
    "_open_full_train_view": ("1f0ad56150bc14bc37ab449d3846d0d103f453bad55592df927049d177403c9a"),
    "_prepare_train": "3bc956c8a6e497cdb68de3cbc3ccd1dff9ef119d7019026c14d1b20b3e32877e",
    "_reference_providers": ("3fa3eb0a778be28297de72d6a2306f11202f3072ab3c0b7c4e4c05db3d9d8b1e"),
    "_render_evidence": "53e2c3f6367c1fd32d28c279c76b773bf2e807cd29f255fc8dd0fc8f2bb0dcf0",
    "_timing_fields": "f1f9323d557163e78e1eccf884481152521aa1f2bffccddb40275ca499648a1f",
    "_validate_feature_frame": ("7600ad5a5e333c0a2f50c7324a40285b3c53001b8c1f6d6676c494a105fc0d81"),
    "test_published_train_reference_features_and_verified_cache_hit_meet_budgets": (
        "9edcf2f96fa0bbd5e06896fe7fd4e036e6ed097936fa021c31925cfa910ae372"
    ),
}

SNAPSHOT_FORBIDDEN_IMPORT_PREFIXES = {
    "duckdb",
    "numpy",
    "pandas",
    "polars",
    "pyarrow",
    f"{ROOT_NAME}.data.gaps",
    f"{ROOT_NAME}.decisions",
    f"{ROOT_NAME}.evaluation",
    f"{ROOT_NAME}.features",
    f"{ROOT_NAME}.models",
    f"{ROOT_NAME}.representations",
}

SNAPSHOT_ALLOWED_FRAME_ATTRIBUTES = {
    "column_names",
    "dataset_ref",
    "num_rows",
}

SNAPSHOT_FORBIDDEN_VALUE_CALLS = {
    "column",
    "dump",
    "dumps",
    "max",
    "mean",
    "min",
    "save",
    "savetxt",
    "savez",
    "savez_compressed",
    "std",
    "tofile",
    "write_bytes",
    "write_text",
}


def _primitive_module_paths() -> list[Path]:
    return [PACKAGE_ROOT / "core" / name for name in NATIVE_PRIMITIVE_MODULES]


def _imported_module_roots(tree: ast.AST) -> set[str]:
    """Return the top-level module names a parsed file imports."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _qualified_name(node: ast.AST) -> str | None:
    """Return a dotted name for a Name/Attribute expression when static."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent is not None else None
    return None


def _resolve_name(name: str, aliases: dict[str, set[str]]) -> set[str]:
    """Resolve the first component of one dotted name through module aliases."""
    root, separator, remainder = name.partition(".")
    resolved_roots = aliases.get(root, {root})
    if not separator:
        return set(resolved_roots)
    return {f"{candidate}.{remainder}" for candidate in resolved_roots}


def _expression_references(node: ast.AST, aliases: dict[str, set[str]]) -> set[str]:
    """Return statically resolvable names referenced by an expression."""
    references: set[str] = set()
    for child in ast.walk(node):
        qualified = _qualified_name(child)
        if qualified is not None:
            references.update(_resolve_name(qualified, aliases))
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            # ``from __future__ import annotations`` and explicit forward
            # references both leave useful type names inside strings.
            try:
                parsed = ast.parse(child.value, mode="eval")
            except SyntaxError:
                continue
            references.update(_expression_references(parsed, aliases))
    return references


def _assigned_names(
    node: ast.Assign | ast.AnnAssign | ast.TypeAlias,
) -> tuple[str, ...]:
    """Return simple names assigned by one module-level statement."""
    if isinstance(node, ast.TypeAlias):
        return (node.name.id,) if isinstance(node.name, ast.Name) else ()
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return tuple(target.id for target in targets if isinstance(target, ast.Name))


def _symbol_aliases(tree: ast.Module) -> dict[str, set[str]]:
    """Resolve import aliases and straightforward module-level type aliases."""
    aliases: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                local = imported.asname or imported.name.split(".")[0]
                target = imported.name if imported.asname else imported.name.split(".")[0]
                aliases[local] = {target}
        elif isinstance(node, ast.ImportFrom) and node.module:
            for imported in node.names:
                local = imported.asname or imported.name
                aliases[local] = {f"{node.module}.{imported.name}"}

    # Resolve aliases such as ``ArrowTable = pa.Table``.  A few passes support
    # aliases of aliases without trying to become a Python evaluator.
    assignments = [
        node for node in tree.body if isinstance(node, ast.Assign | ast.AnnAssign | ast.TypeAlias)
    ]
    for _ in range(len(assignments) + 1):
        changed = False
        for assignment in assignments:
            value = assignment.value
            if value is None:
                continue
            references = _expression_references(value, aliases)
            if not references:
                continue
            for name in _assigned_names(assignment):
                if aliases.get(name) != references:
                    aliases[name] = references
                    changed = True
        if not changed:
            break
    return aliases


def _declared_exports(tree: ast.Module) -> set[str]:
    """Return literal names from a conventional module ``__all__``."""
    exports: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            continue
        if node.value is not None:
            exports.update(
                child.value
                for child in ast.walk(node.value)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            )
    return exports


def _is_public_name(name: str, exports: set[str]) -> bool:
    """Apply Python's public-name convention plus explicit exports."""
    return not name.startswith("_") or name in exports


def _public_callable_nodes(
    tree: ast.Module,
) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Return public module callables and constructor/public class methods."""
    exports = _declared_exports(tree)
    callables: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if _is_public_name(node.name, exports):
                callables.append((node.name, node))
            continue
        if not isinstance(node, ast.ClassDef) or not _is_public_name(node.name, exports):
            continue
        for member in node.body:
            if not isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if _is_public_name(member.name, set()) or member.name in {
                "__call__",
                "__init__",
                "__new__",
            }:
                callables.append((f"{node.name}.{member.name}", member))
    return callables


def _function_annotation_sites(
    qualified_name: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[str, ast.AST]]:
    """Return the annotated portions of one callable's public signature."""
    sites: list[tuple[str, ast.AST]] = []
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    if node.args.vararg is not None:
        arguments.append(node.args.vararg)
    if node.args.kwarg is not None:
        arguments.append(node.args.kwarg)
    sites.extend(
        (f"{qualified_name} parameter {argument.arg!r}", argument.annotation)
        for argument in arguments
        if argument.annotation is not None
    )
    if node.returns is not None:
        sites.append((f"{qualified_name} return", node.returns))
    return sites


def _public_annotation_sites(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """Return every annotation that participates in a public signature."""
    exports = _declared_exports(tree)
    sites: list[tuple[str, ast.AST]] = []
    for qualified_name, function in _public_callable_nodes(tree):
        sites.extend(_function_annotation_sites(qualified_name, function))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if _is_public_name(node.target.id, exports):
                sites.append((node.target.id, node.annotation))
        elif isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
            if _is_public_name(node.name.id, exports):
                sites.append((node.name.id, node.value))
        elif isinstance(node, ast.ClassDef) and _is_public_name(node.name, exports):
            for member in node.body:
                if (
                    isinstance(member, ast.AnnAssign)
                    and isinstance(member.target, ast.Name)
                    and _is_public_name(member.target.id, set())
                ):
                    sites.append((f"{node.name}.{member.target.id}", member.annotation))
    return sites


def _reference_leaf(reference: str) -> str:
    """Return the last component of one resolved symbol."""
    return reference.rsplit(".", maxsplit=1)[-1]


def _mentions_type(annotation: ast.AST | None, aliases: dict[str, set[str]], name: str) -> bool:
    """Whether an annotation names a type, including through import aliases."""
    if annotation is None:
        return False
    return any(
        _reference_leaf(reference) == name
        for reference in _expression_references(annotation, aliases)
    )


def _annotation_is_optional(annotation: ast.AST, aliases: dict[str, set[str]]) -> bool:
    """Recognize Optional/None in evaluated and string annotations."""
    if any(
        _reference_leaf(reference) == "Optional"
        for reference in _expression_references(annotation, aliases)
    ):
        return True
    for child in ast.walk(annotation):
        if isinstance(child, ast.Constant) and child.value is None:
            return True
        if not isinstance(child, ast.Constant) or not isinstance(child.value, str):
            continue
        try:
            parsed = ast.parse(child.value, mode="eval")
        except SyntaxError:
            continue
        if _annotation_is_optional(parsed, aliases):
            return True
    return False


def _parameter_records(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[ast.arg, bool, ast.expr | None]]:
    """Return parameters with whether and how each one has a default."""
    records: list[tuple[ast.arg, bool, ast.expr | None]] = []
    positional = [*node.args.posonlyargs, *node.args.args]
    default_start = len(positional) - len(node.args.defaults)
    for index, argument in enumerate(positional):
        has_default = index >= default_start
        default = node.args.defaults[index - default_start] if has_default else None
        records.append((argument, has_default, default))
    records.extend(
        (argument, default is not None, default)
        for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True)
    )
    return records


def _slice_1d_paths() -> list[Path]:
    """Return every module in the Slice 1D data package."""
    return _layer_files("data")


def _loader_implementation_paths() -> list[Path]:
    """Return modules that implement loading rather than gating or catalogs."""
    return [
        PACKAGE_ROOT / relative
        for relative in LOADER_IMPLEMENTATION_MODULES
        if (PACKAGE_ROOT / relative).is_file()
    ]


def _slice_1d_computational_paths() -> list[Path]:
    """Return the frame/loader path where market keys stay opaque integers."""
    paths = [
        PACKAGE_ROOT / relative
        for relative in SLICE_1D_COMPUTATIONAL_MODULES
        if (PACKAGE_ROOT / relative).is_file()
    ]
    adapter_root = PACKAGE_ROOT / "data" / "adapters"
    if adapter_root.is_dir():
        paths.extend(sorted(adapter_root.rglob("*.py")))
    return paths


def _name_suggests_row_route(name: str) -> bool:
    """Recognize public names that claim to read or materialize market rows."""
    lowered = name.rsplit(".", maxsplit=1)[-1].lower()
    if lowered in ROW_ROUTE_ACTIONS:
        return True
    action = next(
        (candidate for candidate in ROW_ROUTE_ACTIONS if lowered.startswith(f"{candidate}_")),
        None,
    )
    return action is not None and any(subject in lowered for subject in ROW_ROUTE_SUBJECTS)


def _reference_is_row_machinery(reference: str) -> bool:
    """Whether a resolved reference reaches the private row-decoding path."""
    leaf = _reference_leaf(reference)
    if leaf in ROW_MACHINERY_NAMES:
        return True
    if "MarketFrameLoader.load" in reference:
        return True
    if reference.endswith(".*") and reference.startswith(
        ("trading_research.data.integrity", "trading_research.data.adapters")
    ):
        return True
    return reference.startswith(("pyarrow.parquet", "trading_research.data.adapters"))


def _callable_references_row_machinery(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    aliases: dict[str, set[str]],
) -> bool:
    """Whether a callable reaches a loader, decoder, or Parquet implementation."""
    return any(
        _reference_is_row_machinery(reference)
        for reference in _expression_references(node, aliases)
    )


def _looks_like_row_loader(
    qualified_name: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    aliases: dict[str, set[str]],
) -> bool:
    """Identify public data-package callables capable of returning rows."""
    if _mentions_type(node.returns, aliases, "MarketFrame"):
        return True
    return _name_suggests_row_route(qualified_name) or _callable_references_row_machinery(
        node, aliases
    )


def _module_level_bound_names(tree: ast.Module) -> set[str]:
    """Return names introduced by module-level imports and assignments."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(imported.asname or imported.name.split(".")[0] for imported in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(imported.asname or imported.name for imported in node.names)
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.TypeAlias):
            names.update(_assigned_names(node))
    return names


def _module_level_public_names(tree: ast.Module) -> set[str]:
    """Return every direct public binding defined by one module's source."""
    exports = _declared_exports(tree)
    names = _module_level_bound_names(tree)
    names.update(
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    )
    return {name for name in names if _is_public_name(name, exports)}


def _module_owned_public_definitions(tree: ast.Module) -> set[str]:
    """Return public functions/classes whose implementation lives in the module."""
    exports = _declared_exports(tree)
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and _is_public_name(node.name, exports)
    }


def _runtime_public_bindings(module: ModuleType) -> dict[str, object]:
    """Return every conventionally public name actually bound by a module."""
    return {name: value for name, value in vars(module).items() if not name.startswith("_")}


def _expected_data_package_runtime_bindings() -> dict[str, str]:
    """Include only exact private submodules already imported by test collection."""
    expected = dict(DATA_PACKAGE_RUNTIME_BINDINGS)
    for name in DATA_PACKAGE_OPTIONAL_INTERNAL_BINDINGS:
        module_name = f"{ROOT_NAME}.data.{name}"
        if module_name in sys.modules:
            expected[name] = module_name
    return expected


def _module_level_binding_origins(tree: ast.Module) -> dict[str, set[str]]:
    """Return the final structural origin of each direct module binding.

    This deliberately models ordinary imports, definitions, aliases and
    assignments rather than arbitrary Python execution.  It is the finite V1
    drift model: a public name is approved only while it still points at the
    canonical source object recorded in the expected-origin table.
    """
    aliases: dict[str, set[str]] = {}
    origins: dict[str, set[str]] = {}
    for node in tree.body:
        bindings: dict[str, set[str]] = {}
        if isinstance(node, ast.Import):
            for imported in node.names:
                local = imported.asname or imported.name.split(".")[0]
                target = imported.name if imported.asname else imported.name.split(".")[0]
                bindings[local] = {target}
        elif isinstance(node, ast.ImportFrom) and node.module:
            for imported in node.names:
                local = imported.asname or imported.name
                bindings[local] = {f"{node.module}.{imported.name}"}
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            bindings[node.name] = {"<module-owned function>"}
        elif isinstance(node, ast.ClassDef):
            bindings[node.name] = {_LOCAL_CLASS}
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.TypeAlias):
            value = node.value
            references = _expression_references(value, aliases) if value is not None else set()
            origin = references or {"<literal or computed expression>"}
            bindings.update(dict.fromkeys(_assigned_names(node), origin))

        for name, references in bindings.items():
            origins[name] = set(references)
            aliases[name] = set(references)
    return origins


def _canonical_source_binding_violations(
    tree: ast.Module,
    expected: dict[str, str],
) -> list[str]:
    """Report public-name or canonical-origin drift in one parsed module."""
    violations: list[str] = []
    observed_names = _module_level_public_names(tree)
    expected_names = set(expected)
    if observed_names != expected_names:
        violations.append(
            "public names differ: "
            f"expected {sorted(expected_names)}, observed {sorted(observed_names)}"
        )

    origins = _module_level_binding_origins(tree)
    for name, reference in expected.items():
        observed = origins.get(name, set())
        if observed != {reference}:
            violations.append(
                f"{name} origin differs: expected {reference!r}, observed {sorted(observed)!r}"
            )
    return violations


def _resolve_runtime_reference(reference: str) -> object:
    """Resolve one canonical module/object reference for identity comparison."""
    parts = reference.split(".")
    for module_end in range(len(parts), 0, -1):
        try:
            value: object = importlib.import_module(".".join(parts[:module_end]))
        except ModuleNotFoundError:
            continue
        for attribute in parts[module_end:]:
            value = getattr(value, attribute)
        return value
    raise AssertionError(f"canonical binding reference is not importable: {reference}")


def _canonical_runtime_binding_violations(
    module_name: str,
    expected: dict[str, str],
) -> list[str]:
    """Report runtime public-name or canonical-object substitution."""
    canonical = {
        name: _resolve_runtime_reference(reference) for name, reference in expected.items()
    }
    module = importlib.import_module(module_name)
    observed = _runtime_public_bindings(module)
    violations: list[str] = []
    if set(observed) != set(expected):
        violations.append(
            f"runtime public names differ: expected {sorted(expected)}, observed {sorted(observed)}"
        )
    for name, intended in canonical.items():
        if observed.get(name) is not intended:
            violations.append(f"runtime binding {name!r} is not its canonical object")
    return violations


def _dynamic_public_attribute_definitions(tree: ast.Module) -> set[str]:
    """Return forbidden module-level dynamic public-attribute hooks."""
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name in {"__getattr__", "__dir__"}
    }


def _public_route_bindings(
    tree: ast.Module,
    aliases: dict[str, set[str]],
) -> list[tuple[str, set[str]]]:
    """Return public or explicitly exported aliases that could expose rows."""
    exports = _declared_exports(tree)
    bindings: list[tuple[str, set[str]]] = []
    for name in sorted(_module_level_bound_names(tree)):
        if not _is_public_name(name, exports):
            continue
        references = aliases.get(name, set())
        explicitly_exports_machinery = name in exports and any(
            _reference_is_row_machinery(reference) for reference in references
        )
        references_route = any(
            _reference_leaf(reference) in ROW_MACHINERY_NAMES
            or "MarketFrameLoader.load" in reference
            or (_reference_leaf(reference) == "*" and _reference_is_row_machinery(reference))
            for reference in references
        )
        if _name_suggests_row_route(name) or explicitly_exports_machinery or references_route:
            bindings.append((name, references))
    return bindings


def _numpy_civil_dtype_conversions(
    node: ast.Call,
    aliases: dict[str, set[str]],
) -> set[str]:
    """Return NumPy calls that reinterpret values with a civil-time dtype."""
    call_references = _expression_references(node.func, aliases)
    call_leaves = {_reference_leaf(reference) for reference in call_references}
    direct_civil_call = bool(call_leaves & NUMPY_CIVIL_DTYPE_NAMES)

    payload_references = {
        reference
        for payload in (
            *node.args,
            *(keyword.value for keyword in node.keywords),
        )
        for reference in _expression_references(payload, aliases)
    }
    mentions_civil_dtype = any(
        _reference_leaf(reference) in NUMPY_CIVIL_DTYPE_REFERENCE_NAMES
        for reference in payload_references
    ) or any(
        _string_mentions_numpy_civil_dtype(child.value)
        for payload in (
            *node.args,
            *(keyword.value for keyword in node.keywords),
        )
        for child in ast.walk(payload)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    )
    if not mentions_civil_dtype and not direct_civil_call:
        return set()

    numpy_constructor = any(
        reference.startswith("numpy.")
        and _reference_leaf(reference) in NUMPY_DTYPE_CONVERSION_CALLS
        for reference in call_references
    )
    descriptor_method = bool(call_leaves & {"astype", "view"})
    if direct_civil_call or numpy_constructor or descriptor_method:
        return call_references
    return set()


def _string_mentions_numpy_civil_dtype(value: str) -> bool:
    """Recognize long and shorthand NumPy civil-time dtype spellings."""
    lowered = value.lower()
    if any(name in lowered for name in NUMPY_CIVIL_DTYPE_NAMES):
        return True
    normalized = value.strip().lstrip("<>=|")
    return normalized in {"M8", "m8"} or normalized.startswith(("M8[", "m8["))


def _annotation_leaf_names(
    annotation: ast.AST | None,
    aliases: dict[str, set[str]],
) -> set[str]:
    """Return the resolved type-name leaves in one annotation."""
    if annotation is None:
        return set()
    return {_reference_leaf(reference) for reference in _expression_references(annotation, aliases)}


def _loader_parameter_name_is_forbidden(name: str) -> bool:
    """Recognize location, bare-target, and caller-asserted bypass inputs."""
    lowered = name.lower()
    parts = set(lowered.split("_"))
    if parts & {"path", "root"}:
        return True
    if lowered in {"dataset", "dataset_ref", "identity", "manifest", "ref"}:
        return True
    return any(
        token in lowered
        for token in (
            "already_author",
            "authorised",
            "authorized",
            "bypass",
            "skip_author",
            "skip_gate",
            "trusted",
            "unsafe",
        )
    )


def _body_contains_raise(body: list[ast.stmt]) -> bool:
    """Whether a guarded statement body contains a refusal."""
    return any(isinstance(child, ast.Raise) for statement in body for child in ast.walk(statement))


def _slice_1e_composition_signature_violations(tree: ast.Module) -> list[str]:
    """Report drift from the one approved bounded-composition signature."""
    aliases = _symbol_aliases(tree)
    definitions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == "open_bounded_view"
    ]
    if len(definitions) != 1:
        return [f"expected one open_bounded_view definition, observed {len(definitions)}"]
    function = definitions[0]
    violations: list[str] = []
    if not isinstance(function, ast.FunctionDef):
        violations.append("open_bounded_view must be synchronous")
    if function.args.posonlyargs or function.args.args:
        violations.append("open_bounded_view must be keyword-only")
    if function.args.vararg is not None or function.args.kwarg is not None:
        violations.append("open_bounded_view must not be variadic")

    expected_names = [
        "loader",
        "grants",
        "context_start",
        "eval_start",
        "eval_end",
        "columns",
        "context_shortfall",
    ]
    observed_names = [argument.arg for argument in function.args.kwonlyargs]
    if observed_names != expected_names:
        violations.append(
            f"composition parameters differ: expected {expected_names}, observed {observed_names}"
        )

    records = {
        argument.arg: (argument, has_default, default)
        for argument, has_default, default in _parameter_records(function)
    }
    required_types = {
        "loader": {"MarketFrameLoader"},
        "grants": {"AccessGrant", "Sequence"},
        "context_start": {"NativeTimeKey"},
        "eval_start": {"NativeTimeKey"},
        "eval_end": {"NativeTimeKey"},
    }
    for name, expected_types in required_types.items():
        record = records.get(name)
        if record is None:
            continue
        argument, has_default, _ = record
        observed_types = _annotation_leaf_names(argument.annotation, aliases)
        if observed_types != expected_types:
            violations.append(
                f"{name} annotation differs: expected {sorted(expected_types)}, "
                f"observed {sorted(observed_types)}"
            )
        if has_default or (
            argument.annotation is not None
            and _annotation_is_optional(argument.annotation, aliases)
        ):
            violations.append(f"{name} must be required and non-optional")

    for name, expected_types in {
        "columns": {"Sequence", "str"},
        "context_shortfall": {"ContextShortfall"},
    }.items():
        record = records.get(name)
        if record is None:
            continue
        argument, has_default, default = record
        observed_types = _annotation_leaf_names(argument.annotation, aliases)
        if observed_types != expected_types:
            violations.append(
                f"{name} annotation differs: expected {sorted(expected_types)}, "
                f"observed {sorted(observed_types)}"
            )
        if not has_default or not isinstance(default, ast.Constant) or default.value is not None:
            violations.append(f"{name} must default only to None")

    if _annotation_leaf_names(function.returns, aliases) != {"BoundedView"}:
        violations.append("open_bounded_view must return BoundedView")

    for argument, _, _ in _parameter_records(function):
        references = (
            _expression_references(argument.annotation, aliases)
            if argument.annotation is not None
            else set()
        )
        forbidden = sorted(
            reference
            for reference in references
            if _reference_leaf(reference) in FORBIDDEN_COMPOSITION_PARAMETER_TYPES
        )
        if forbidden or _loader_parameter_name_is_forbidden(argument.arg):
            violations.append(f"forbidden composition input {argument.arg!r}: {forbidden}")

    loader_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _qualified_name(node.func) == "loader.load"
    ]
    if len(loader_calls) != 1:
        violations.append(
            "open_bounded_view must contain exactly one MarketFrameLoader.load call site"
        )
    elif (
        len(loader_calls[0].args) != 1
        or not isinstance(loader_calls[0].args[0], ast.Name)
        or loader_calls[0].args[0].id != "member"
    ):
        violations.append("every composed load must receive the iterated exact member grant")
    return violations


def _slice_1e_io_violations(tree: ast.Module) -> list[str]:
    """Report direct decoder, filesystem, environment, or table-library access."""
    violations: list[str] = []
    forbidden_imports = {
        "duckdb",
        "io",
        "os",
        "pandas",
        "pathlib",
        "polars",
        "pyarrow",
    }
    aliases = _symbol_aliases(tree)
    imported = _imported_module_roots(tree) & forbidden_imports
    violations.extend(f"imports {name}" for name in sorted(imported))
    forbidden_leaves = {
        "ParquetFile",
        "DatasetRegistry",
        "_decode_partition",
        "_make_market_frame",
        "authorize_access",
        "classify_access",
        "environ",
        "getcwd",
        "getenv",
        "glob",
        "open",
        "read_bytes",
        "read_manifest",
        "read_split_policy",
        "read_table",
        "read_text",
        "rglob",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        references = _expression_references(node.func, aliases)
        forbidden = sorted(
            reference for reference in references if _reference_leaf(reference) in forbidden_leaves
        )
        if forbidden:
            violations.append(f"calls {forbidden}")
    return violations


def _absolute_import_modules(tree: ast.Module) -> set[str]:
    """Return complete absolute module names imported by one module."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(imported.name for imported in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def _docstring_constant_ids(tree: ast.Module) -> set[int]:
    """Return AST identities of prose strings that are genuine docstrings."""
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    docstrings: set[int] = set()
    for owner in ast.walk(tree):
        if not isinstance(owner, owners) or not owner.body:
            continue
        first = owner.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))
    return docstrings


def _identifier_records(tree: ast.Module) -> list[tuple[str, str]]:
    """Return labels and values for every ordinary Python identifier."""
    records: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        name: str | None = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.arg):
            name = node.arg
        elif isinstance(node, ast.alias):
            name = node.asname or node.name
        elif isinstance(node, ast.keyword):
            name = node.arg
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            name = node.name
        if name is not None:
            records.append((type(node).__name__, name))
    return records


def _forbidden_identifier_violations(tree: ast.Module, forbidden: set[str]) -> list[str]:
    """Find ordinary identifier/import/export uses without matching prose."""
    violations = {
        f"{kind} {identifier}"
        for kind, identifier in _identifier_records(tree)
        if identifier in forbidden
    }
    violations.update(
        f"import {node.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.alias) and node.name.rsplit(".", maxsplit=1)[-1] in forbidden
    )
    violations.update(f"export {name}" for name in _declared_exports(tree) if name in forbidden)
    return sorted(violations)


def _data_layer_identifier_violations(forbidden: set[str]) -> list[str]:
    """Report scoped legacy references from production data-layer modules."""
    violations: list[str] = []
    for path in _layer_files("data"):
        tree = ast.parse(path.read_text(), filename=str(path))
        violations.extend(
            f"{path.relative_to(PACKAGE_ROOT)}: {detail}"
            for detail in _forbidden_identifier_violations(tree, forbidden)
        )
    return violations


def _gaps_semantic_boundary_violations(tree: ast.Module) -> list[str]:
    """Reject scoped interpretation names and OD-G2's deferred field."""
    records = _identifier_records(tree)
    docstrings = _docstring_constant_ids(tree)
    records.extend(
        ("string literal", node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    )
    forbidden = {*GAPS_FORBIDDEN_SEMANTIC_TERMS, "boundary_class"}
    violations: list[str] = []
    for kind, value in records:
        matched = sorted(term for term in forbidden if term in value.lower())
        if matched:
            violations.append(f"{kind} {value!r} contains {matched}")
    return violations


def _gaps_type_checking_import_nodes(
    tree: ast.Module,
    aliases: dict[str, set[str]],
) -> set[int]:
    """Return imports in the body of a bare typing.TYPE_CHECKING guard."""
    imports: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        qualified_test = _qualified_name(node.test)
        if qualified_test is None or _resolve_name(qualified_test, aliases) != {
            "typing.TYPE_CHECKING"
        }:
            continue
        for statement in node.body:
            imports.update(
                id(child)
                for child in ast.walk(statement)
                if isinstance(child, ast.Import | ast.ImportFrom)
            )
    return imports


def _gaps_import_and_capability_violations(tree: ast.Module) -> list[str]:
    """Constrain GapIndex to arithmetic over an already-issued MarketFrame."""
    violations: list[str] = []
    imported_modules = _absolute_import_modules(tree)
    violations.extend(
        f"imports disallowed module {module}"
        for module in sorted(imported_modules - GAPS_ALLOWED_IMPORT_MODULES)
    )

    aliases = _symbol_aliases(tree)
    type_checking_imports = _gaps_type_checking_import_nodes(tree, aliases)
    for node in ast.walk(tree):
        modules: set[str] = set()
        imported_names: set[str] = set()
        if isinstance(node, ast.Import):
            modules.update(imported.name for imported in node.names)
            imported_names.update(imported.name for imported in node.names)
            violations.extend(
                f"imports governed core module {module} instead of its allowed symbol"
                for module in sorted(modules & set(GAPS_ALLOWED_CORE_IMPORT_NAMES))
            )
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0:
                violations.append("relative imports are outside the GapIndex allowlist")
                continue
            if node.module is None:
                continue
            modules.add(node.module)
            imported_names.update(imported.name for imported in node.names)
            allowed_names = GAPS_ALLOWED_CORE_IMPORT_NAMES.get(node.module)
            if allowed_names is not None:
                observed_names = {imported.name for imported in node.names}
                unexpected = observed_names - allowed_names
                if unexpected:
                    violations.append(
                        f"{node.module} imports disallowed names {sorted(unexpected)}"
                    )
        else:
            continue

        forbidden_imported_names = sorted(
            name
            for name in imported_names
            if _reference_leaf(name).lower()
            in {
                *GAPS_FORBIDDEN_AUTHORITY_IDENTIFIERS,
                *GAPS_FORBIDDEN_CAPABILITY_CALLS,
            }
        )
        if forbidden_imported_names:
            violations.append(f"imports forbidden capability names {forbidden_imported_names}")

        for module in sorted(modules & GAPS_TYPE_CHECKING_ONLY_IMPORTS):
            if id(node) not in type_checking_imports:
                violations.append(f"{module} must be imported only under TYPE_CHECKING")

    for kind, identifier in _identifier_records(tree):
        normalized = identifier.strip("_").lower()
        variants = {normalized, normalized.replace("_", ""), *normalized.split("_")}
        matched = sorted(variants & GAPS_FORBIDDEN_AUTHORITY_IDENTIFIERS)
        if matched:
            violations.append(f"{kind} {identifier!r} names forbidden authority {matched}")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        forbidden_calls = sorted(
            reference
            for reference in _expression_references(node.func, aliases)
            if _reference_leaf(reference) in GAPS_FORBIDDEN_CAPABILITY_CALLS
        )
        if forbidden_calls:
            violations.append(f"calls forbidden capability {forbidden_calls}")
    return violations


def _gap_index_class(tree: ast.Module) -> ast.ClassDef | None:
    """Return the sole module-level GapIndex definition when structurally exact."""
    definitions = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "GapIndex"
    ]
    return definitions[0] if len(definitions) == 1 else None


def _gap_index_from_frame(
    tree: ast.Module,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return the sole GapIndex.from_frame definition when present."""
    gap_index = _gap_index_class(tree)
    if gap_index is None:
        return None
    producers = [
        node
        for node in gap_index.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "from_frame"
    ]
    return producers[0] if len(producers) == 1 else None


def _gap_index_producer_violations(tree: ast.Module) -> list[str]:
    """Require vectorized diff and no scalar iteration in arithmetic helpers."""
    producer = _gap_index_from_frame(tree)
    if producer is None:
        return ["GapIndex must define exactly one from_frame producer"]
    arithmetic_path = [
        producer,
        *(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("_")
            and not node.name.startswith("__")
            and node.name != "_descriptor"
        ),
    ]
    aliases = _symbol_aliases(tree)
    diff_calls = [
        node
        for function in arithmetic_path
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and "numpy.diff" in _expression_references(node.func, aliases)
    ]
    violations: list[str] = []
    if len(diff_calls) != 1:
        violations.append(f"arithmetic path must call numpy.diff once, found {len(diff_calls)}")

    forbidden_nodes = (
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
    )
    for function in arithmetic_path:
        observed = sorted(
            {
                type(node).__name__
                for node in ast.walk(function)
                if isinstance(node, forbidden_nodes)
            }
        )
        if observed:
            violations.append(f"{function.name} contains scalar iteration {observed}")
    return violations


def _slice_1f_keys_only_loader_violations(tree: ast.Module) -> list[str]:
    """Pin the generator's sole decoder use to ``load(grant, columns=())``."""
    violations: list[str] = []
    load_attributes = [
        node for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == "load"
    ]
    load_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "load"
    ]
    if len(load_attributes) != 1 or len(load_calls) != 1:
        violations.append("views_generation must contain exactly one direct loader.load call site")
        return violations

    call = load_calls[0]
    if _qualified_name(call.func) != "loader.load":
        violations.append("the sole load must be invoked as loader.load")
    if len(call.args) != 1 or not isinstance(call.args[0], ast.Name) or call.args[0].id != "grant":
        violations.append("the sole load must receive only the exact grant positionally")
    if (
        len(call.keywords) != 1
        or call.keywords[0].arg != "columns"
        or not isinstance(call.keywords[0].value, ast.Tuple)
        or call.keywords[0].value.elts
    ):
        violations.append("the sole load must project exactly columns=()")

    scanners = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == "_scan_complete_source_keys"
    ]
    if len(scanners) != 1 or not any(candidate is call for candidate in ast.walk(scanners[0])):
        violations.append("the sole load must live in _scan_complete_source_keys")
    return violations


def _slice_1f_generation_boundary_violations(tree: ast.Module) -> list[str]:
    """Report direct decoding, ambient configuration, or later-phase coupling."""
    violations: list[str] = []
    aliases = _symbol_aliases(tree)
    modules = _absolute_import_modules(tree)

    for module in sorted(modules):
        if module.startswith(f"{ROOT_NAME}.") and (module not in SLICE_1F_ALLOWED_INTERNAL_IMPORTS):
            violations.append(f"imports unapproved internal dependency {module}")
        if module.split(".", maxsplit=1)[0] in {
            "duckdb",
            "pandas",
            "polars",
            "pyarrow",
        }:
            violations.append(f"imports direct row decoder {module}")
        if module == f"{ROOT_NAME}.data.adapters" or module.startswith(
            f"{ROOT_NAME}.data.adapters."
        ):
            violations.append(f"imports adapter decoder implementation {module}")
        if module.split(".", maxsplit=1)[0] in {"argparse", "configparser", "os"}:
            violations.append(f"imports ambient configuration machinery {module}")

    for local, references in aliases.items():
        forbidden = sorted(
            reference
            for reference in references
            if _reference_leaf(reference) in SLICE_1F_FORBIDDEN_DECODER_LEAVES
        )
        if forbidden:
            violations.append(f"binds decoder machinery {forbidden} as {local}")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        references = _expression_references(node.func, aliases)
        decoder_calls = sorted(
            reference
            for reference in references
            if _reference_leaf(reference) in SLICE_1F_FORBIDDEN_DECODER_LEAVES
        )
        if decoder_calls:
            violations.append(f"calls direct decoder machinery {decoder_calls}")
        ambient_calls = sorted(
            reference
            for reference in references
            if _reference_leaf(reference) in SLICE_1F_FORBIDDEN_AMBIENT_LEAVES
        )
        if ambient_calls:
            violations.append(f"constructs or reads ambient configuration {ambient_calls}")

    observed_read_calls: set[int] = set()
    for function in (
        node for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ):
        permitted = SLICE_1F_MANIFEST_READ_CALLS.get(function.name, set())
        for call in (
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "read_bytes"
        ):
            observed_read_calls.add(id(call))
            qualified = _qualified_name(call.func)
            if qualified not in permitted:
                violations.append(f"{function.name} reads unapproved bytes via {qualified}")
    for call in (
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_bytes"
        and id(node) not in observed_read_calls
    ):
        violations.append(f"module scope reads unapproved bytes via {_qualified_name(call.func)}")

    civil_imports = _imported_module_roots(tree) & CIVIL_TIME_MODULES
    violations.extend(f"imports civil-time machinery {module}" for module in sorted(civil_imports))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        references = _expression_references(node.func, aliases)
        civil_calls = sorted(
            reference
            for reference in references
            if _reference_leaf(reference) in FORBIDDEN_MARKET_TIME_CALLS
        )
        if civil_calls:
            violations.append(f"derives market boundaries through {civil_calls}")
    return violations


def _slice_1f_literal_assumption_violations(tree: ast.Module) -> list[str]:
    """Report V1 policy, asset, timeframe, filename, and layout literals."""
    violations: list[str] = []
    string_constants = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    integer_constants = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    }
    hardcoded_policy = sorted(integer_constants & SLICE_1F_V1_POLICY_LITERALS)
    if hardcoded_policy:
        violations.append(f"hardcodes V1 split-policy keys {hardcoded_policy}")
    hardcoded_years = sorted(integer_constants & set(range(2020, 2027)))
    if hardcoded_years:
        violations.append(f"hardcodes physical partition years {hardcoded_years}")
    if 7 in integer_constants:
        violations.append("hardcodes the current seven-partition layout")

    hexadecimal = set("0123456789abcdefABCDEF")
    for value in sorted(string_constants):
        lowered = value.casefold()
        tokens = set(
            "".join(character if character.isalnum() else " " for character in lowered).split()
        )
        if "xauusd" in lowered:
            violations.append(f"hardcodes the current asset in {value!r}")
        if "m1" in tokens:
            violations.append(f"hardcodes the current timeframe in {value!r}")
        if ".parquet" in lowered or lowered == "manifest.json":
            violations.append(f"hardcodes a physical/current filename in {value!r}")
        years = [year for year in range(2020, 2027) if str(year) in value]
        if years:
            violations.append(f"hardcodes partition years {years} in {value!r}")
        if len(value) == 64 and set(value) <= hexadecimal:
            violations.append("hardcodes a current manifest/file digest")

    column_names = {value.casefold() for value in string_constants}
    if {"open", "high", "low", "close"} <= column_names:
        violations.append("contains an OHLC column whitelist")
    return violations


def _slice_1f_publication_violations(tree: ast.Module) -> list[str]:
    """Require every final-manifest write to pass through the atomic helper."""
    violations: list[str] = []
    aliases = _symbol_aliases(tree)
    expected_reference = f"{ROOT_NAME}.data.atomic_write._publish_immutable_bytes"

    imports = [
        (imported.name, imported.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == f"{ROOT_NAME}.data.atomic_write"
        for imported in node.names
    ]
    if imports != [
        ("_ensure_durable_directory", None),
        ("_publish_immutable_bytes", None),
    ]:
        violations.append(f"atomic publication import differs: {imports}")

    atomic_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and expected_reference in _expression_references(node.func, aliases)
    ]
    if len(atomic_calls) != 1:
        violations.append(
            f"expected one atomic publication call site, observed {len(atomic_calls)}"
        )
    else:
        call = atomic_calls[0]
        if (
            len(call.args) != 2
            or call.keywords
            or not isinstance(call.args[0], ast.Name)
            or call.args[0].id != "path"
            or not isinstance(call.args[1], ast.Name)
            or call.args[1].id != "payload"
        ):
            violations.append("atomic publication must receive exact path and payload")
        wrappers = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == "_publish_manifest_bytes"
        ]
        if len(wrappers) != 1 or not any(candidate is call for candidate in ast.walk(wrappers[0])):
            violations.append("atomic publication must live only in _publish_manifest_bytes")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        references = _expression_references(node.func, aliases)
        direct_writes = sorted(
            reference
            for reference in references
            if _reference_leaf(reference) in SLICE_1F_DIRECT_PUBLICATION_LEAVES
        )
        if direct_writes:
            violations.append(f"publishes manifest bytes directly via {direct_writes}")
    return violations


def _snapshot_frame_names(tree: ast.Module) -> set[str]:
    """Return variables that receive a frame from the shared timed primitive."""
    aliases = _symbol_aliases(tree)
    names = {
        node.arg for node in ast.walk(tree) if isinstance(node, ast.arg) and node.arg == "frame"
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if not any(
            _reference_leaf(reference) == "_timed_load"
            for reference in _expression_references(node.value.func, aliases)
        ):
            continue
        for target in node.targets:
            if isinstance(target, ast.Tuple) and target.elts:
                first = target.elts[0]
                if isinstance(first, ast.Name):
                    names.add(first.id)
    return names


def _snapshot_value_boundary_violations(tree: ast.Module) -> list[str]:
    """Report analysis, value inspection, or persistence in the gated operator."""
    violations: list[str] = []
    modules = _absolute_import_modules(tree)
    for module in sorted(modules):
        if any(
            module == prefix or module.startswith(f"{prefix}.")
            for prefix in SNAPSHOT_FORBIDDEN_IMPORT_PREFIXES
        ):
            violations.append(f"imports forbidden analysis dependency {module}")

    aliases = _symbol_aliases(tree)
    frame_names = _snapshot_frame_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            owner = _qualified_name(node.value)
            root = None if owner is None else owner.split(".", maxsplit=1)[0]
            if root in frame_names and node.attr not in SNAPSHOT_ALLOWED_FRAME_ATTRIBUTES:
                violations.append(f"reads forbidden frame attribute {node.attr}")
            if node.attr in {"OOS", "LOCKBOX"}:
                violations.append(f"references forbidden role identity {node.attr}")
        elif isinstance(node, ast.Subscript):
            owner = _qualified_name(node.value)
            root = None if owner is None else owner.split(".", maxsplit=1)[0]
            if root in frame_names:
                violations.append(f"indexes frame value through {owner}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            upper = node.value.upper()
            if "XAUUSD_S_M1_OOS" in upper or "XAUUSD_S_M1_LOCKBOX" in upper:
                violations.append("names a forbidden direct OOS/LOCKBOX target")

        if not isinstance(node, ast.Call):
            continue
        forbidden = sorted(
            reference
            for reference in _expression_references(node.func, aliases)
            if _reference_leaf(reference) in SNAPSHOT_FORBIDDEN_VALUE_CALLS
        )
        if forbidden:
            violations.append(f"calls forbidden value/reducer/persistence function {forbidden}")
    return violations


def _function_call_records(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    aliases: dict[str, set[str]],
) -> list[tuple[str, int, ast.Call]]:
    """Return resolved call leaves and source order inside one function."""
    records: list[tuple[str, int, ast.Call]] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        for reference in _expression_references(node.func, aliases):
            records.append((_reference_leaf(reference), node.lineno, node))
    return records


def _timed_load_boundary_violations(tree: ast.Module) -> list[str]:
    """Pin authorization outside and policy/manifest discovery inside timing."""
    definitions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "_timed_load"
    ]
    if len(definitions) != 1 or not isinstance(definitions[0], ast.FunctionDef):
        return [f"expected one synchronous _timed_load, observed {len(definitions)}"]
    function = definitions[0]
    aliases = _symbol_aliases(tree)
    records = _function_call_records(function, aliases)
    violations: list[str] = []

    expected_leaves = (
        "perf_counter",
        "read_split_policy",
        "discover",
        "MarketFrameLoader",
        "load",
        "perf_counter",
    )
    observed: list[tuple[str, int, ast.Call]] = []
    for leaf in expected_leaves:
        after = observed[-1][1] if observed else -1
        matches = [record for record in records if record[0] == leaf and record[1] > after]
        if not matches:
            violations.append(f"timed load is missing ordered {leaf} call")
            break
        observed.append(min(matches, key=lambda record: record[1]))

    authorization_calls = [record for record in records if record[0] == "authorize_access"]
    if authorization_calls:
        violations.append("_timed_load performs authorization")

    load_calls = [record[2] for record in records if record[0] == "load"]
    if len(load_calls) != 1:
        violations.append(f"_timed_load must contain one loader.load, found {len(load_calls)}")
    else:
        load = load_calls[0]
        if (
            _qualified_name(load.func) != "loader.load"
            or len(load.args) != 1
            or not isinstance(load.args[0], ast.Name)
            or load.args[0].id != "grant"
        ):
            violations.append("_timed_load must call loader.load with the existing grant")

    authorization_sites = [
        function_node.name
        for function_node in tree.body
        if isinstance(function_node, ast.FunctionDef | ast.AsyncFunctionDef)
        for leaf, _, _ in _function_call_records(function_node, aliases)
        if leaf == "authorize_access"
    ]
    if authorization_sites != ["_prepare_train_access"]:
        violations.append(
            "TRAIN authorization must occur once in _prepare_train_access; "
            f"observed {authorization_sites}"
        )
    return violations


def _snapshot_authorization_topology_violations(tree: ast.Module) -> list[str]:
    """Pin one explicit authorization outside every SNAPSHOT timed load."""
    aliases = _symbol_aliases(tree)
    violations: list[str] = []
    sites = [
        function.name
        for function in tree.body
        if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
        for leaf, _, _ in _function_call_records(function, aliases)
        if leaf == "authorize_access"
    ]
    if sites != ["_authorize_snapshot"]:
        violations.append(f"SNAPSHOT authorize_access sites differ: {sites}")

    forbidden_import_time_calls = {
        "JsonlAuditWriter",
        "_manual_cold_samples",
        "_run_snapshot",
        "_timed_load",
        "authorize_access",
        "load",
    }
    for statement in tree.body:
        if isinstance(
            statement,
            ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        ):
            continue
        for node in ast.walk(statement):
            if not isinstance(node, ast.Call):
                continue
            leaves = {
                _reference_leaf(reference)
                for reference in _expression_references(node.func, aliases)
            }
            forbidden = sorted(leaves & forbidden_import_time_calls)
            if forbidden:
                violations.append(f"module scope invokes gated operation {forbidden}")
    return violations


def _slice_1e_bound_view_storage_violations(tree: ast.Module) -> list[str]:
    """Report loading authority or locations retained by BoundedView contracts."""
    aliases = _symbol_aliases(tree)
    forbidden_types = {
        "AccessGrant",
        "DatasetRegistry",
        "NormalizedManifest",
        "Path",
        "RegisteredManifest",
        "SplitPolicy",
        "MarketFrameLoader",
    }
    forbidden_name_tokens = {
        "grant",
        "loader",
        "manifest",
        "path",
        "policy",
        "registry",
        "root",
    }
    violations: list[str] = []
    for class_node in (
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name in {"BoundedSegment", "BoundedView", "ContextResolution"}
    ):
        for member in class_node.body:
            if not isinstance(member, ast.AnnAssign) or not isinstance(member.target, ast.Name):
                continue
            name = member.target.id
            leaves = _annotation_leaf_names(member.annotation, aliases)
            forbidden = sorted(leaves & forbidden_types)
            if forbidden or any(token in name.lower() for token in forbidden_name_tokens):
                violations.append(f"{class_node.name}.{name} retains {forbidden}")
    return violations


def _slice_1e_role_match_violations(tree: ast.Module) -> list[str]:
    """Require every role decision to enumerate all ViewRole members."""
    aliases = _symbol_aliases(tree)
    expected = {"RAW", "CURATED", "SNAPSHOT", "TRAIN", "VALIDATION", "OOS", "LOCKBOX"}
    violations: list[str] = []
    for function_name in (
        "_bounded_role_allowed",
        "_union_role_allowed",
        "_tier_one_role_position",
    ):
        functions = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        ]
        if len(functions) != 1:
            violations.append(f"{function_name} definition count is {len(functions)}")
            continue
        matches = [node for node in ast.walk(functions[0]) if isinstance(node, ast.Match)]
        if len(matches) != 1:
            violations.append(f"{function_name} must contain exactly one match")
            continue
        covered: set[str] = set()
        for case in matches[0].cases:
            if isinstance(case.pattern, ast.MatchAs) and case.pattern.pattern is None:
                violations.append(f"{function_name} has a fallback/default case")
            covered.update(
                _reference_leaf(reference)
                for reference in _expression_references(case.pattern, aliases)
                if _reference_leaf(reference) in expected
            )
        if covered != expected:
            violations.append(
                f"{function_name} role coverage differs: expected {sorted(expected)}, "
                f"observed {sorted(covered)}"
            )
    return violations


def test_native_time_module_imports_no_civil_time_machinery() -> None:
    """A calendar import here would contradict the whole point of native keys."""
    tree = ast.parse((PACKAGE_ROOT / "core" / "time.py").read_text())
    forbidden = _imported_module_roots(tree) & CIVIL_TIME_MODULES
    assert not forbidden, f"core/time.py must not import civil-time machinery: {sorted(forbidden)}"


def test_native_primitives_declare_no_utc_identifier() -> None:
    """No name, attribute or import in these modules may claim a UTC reading.

    Scoped to identifiers inside the two primitive modules on purpose. Prose
    may discuss UTC — explaining why it is refused is valuable — and
    operational timestamps elsewhere (a manifest's ``generated_at_utc``, an
    audit event's clock) are legitimate and must stay legal.
    """
    violations: list[str] = []
    for path in _primitive_module_paths():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            name: str | None = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, ast.arg):
                name = node.arg
            elif isinstance(node, ast.alias):
                name = node.asname or node.name
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                name = node.name
            if name is not None and "utc" in name.lower():
                violations.append(f"{path.relative_to(PACKAGE_ROOT)}: {name}")
    assert not violations, "native primitives must not claim UTC: " + "; ".join(violations)


def test_slice_1d_computational_path_uses_no_civil_time_or_utc_machinery() -> None:
    """MarketFrame, integrity and adapters keep market time as opaque integers."""
    violations: list[str] = []
    for path in _slice_1d_computational_paths():
        relative = path.relative_to(PACKAGE_ROOT)
        tree = ast.parse(path.read_text(), filename=str(path))
        aliases = _symbol_aliases(tree)
        forbidden_imports = _imported_module_roots(tree) & CIVIL_TIME_MODULES
        violations.extend(f"{relative} imports {module}" for module in sorted(forbidden_imports))
        numpy_civil_symbols = sorted(
            {
                reference
                for references in aliases.values()
                for reference in references
                if reference in {"numpy.datetime64", "numpy.timedelta64"}
            }
            | {
                reference
                for reference in _expression_references(tree, aliases)
                if reference in {"numpy.datetime64", "numpy.timedelta64"}
            }
        )
        if numpy_civil_symbols:
            violations.append(
                f"{relative} references NumPy civil-time symbols {numpy_civil_symbols}"
            )
        for node in ast.walk(tree):
            identifier: str | None = None
            if isinstance(node, ast.Name):
                identifier = node.id
            elif isinstance(node, ast.Attribute):
                identifier = node.attr
            elif isinstance(node, ast.arg):
                identifier = node.arg
            elif isinstance(node, ast.alias):
                identifier = node.asname or node.name
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                identifier = node.name
            if identifier is not None and "utc" in identifier.lower():
                violations.append(f"{relative} declares UTC identifier {identifier!r}")

            if not isinstance(node, ast.Call):
                continue
            forbidden_calls = sorted(
                reference
                for reference in _expression_references(node.func, aliases)
                if _reference_leaf(reference) in FORBIDDEN_MARKET_TIME_CALLS
            )
            if forbidden_calls:
                violations.append(f"{relative} calls civil-time conversion {forbidden_calls}")
            dtype_conversions = sorted(_numpy_civil_dtype_conversions(node, aliases))
            if dtype_conversions:
                violations.append(
                    f"{relative} performs NumPy civil-time dtype conversion {dtype_conversions}"
                )
    assert not violations, "Slice 1D market-time purity violations: " + "; ".join(violations)


@pytest.mark.parametrize(
    "source",
    [
        'import numpy as np\nnp.array([1], dtype="datetime64[s]")\n',
        'import numpy\nnumpy.asarray([1], dtype="timedelta64[m]")\n',
        'import numpy as np\nnp.dtype("datetime64[ns]")\n',
        'values.astype("datetime64[s]")\n',
        'values.view("timedelta64[s]")\n',
        'import numpy as np\nnp.array(values, dtype="M8[s]")\n',
        'import numpy as np\nnp.array(values, dtype="m8[s]")\n',
        'import numpy as np\nnp.dtype("M8[s]")\n',
        'import numpy as np\nnp.dtype("m8[s]")\n',
        'values.astype("M8[s]")\n',
        'values.astype("m8[s]")\n',
        'values.view("M8[s]")\n',
        'values.view("m8[s]")\n',
        'civil_dtype = "M8[s]"\nvalues.astype(civil_dtype)\n',
    ],
)
def test_slice_1d_civil_time_guard_recognizes_numpy_dtype_conversions(
    source: str,
) -> None:
    """Dtype keywords and descriptor conversions cannot bypass the guard."""
    tree = ast.parse(source)
    aliases = _symbol_aliases(tree)
    assert any(
        _numpy_civil_dtype_conversions(node, aliases)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    )


def test_slice_1d_civil_time_guard_allows_physical_schema_metadata() -> None:
    """A documented physical dtype string is not itself a market-key conversion."""
    tree = ast.parse('physical_dtype = "datetime64[ns]"\n')
    aliases = _symbol_aliases(tree)
    assert not any(
        _numpy_civil_dtype_conversions(node, aliases)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    )

    shorthand_tree = ast.parse('physical_dtype = "M8[ns]"\n')
    shorthand_aliases = _symbol_aliases(shorthand_tree)
    assert not any(
        _numpy_civil_dtype_conversions(node, shorthand_aliases)
        for node in ast.walk(shorthand_tree)
        if isinstance(node, ast.Call)
    )


def test_slice_1d_computational_path_depends_on_no_legacy_row_carrier() -> None:
    """New frames/loaders/adapters stay independent until legacy retirement in 1H."""
    violations: list[str] = []
    for path in _slice_1d_computational_paths():
        relative = path.relative_to(PACKAGE_ROOT)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for imported in node.names:
                    if imported.name in LEGACY_CARRIER_MODULES:
                        violations.append(f"{relative} imports {imported.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module in LEGACY_CARRIER_MODULES:
                    violations.append(f"{relative} imports from {node.module}")
                for imported in node.names:
                    if imported.name in LEGACY_ROW_CARRIERS:
                        violations.append(f"{relative} imports {imported.name}")
            elif isinstance(node, ast.Name) and node.id in LEGACY_ROW_CARRIERS:
                violations.append(f"{relative} references {node.id}")
            elif isinstance(node, ast.Attribute) and node.attr in LEGACY_ROW_CARRIERS:
                violations.append(f"{relative} references {node.attr}")
    assert not violations, "Slice 1D legacy-carrier dependencies: " + "; ".join(violations)


def test_data_layer_never_references_the_retired_provider_path() -> None:
    """The obsolete provider path cannot bypass the native-time row loader."""
    violations = _data_layer_identifier_violations(RETIRED_PROVIDER_IDENTIFIERS)
    assert not violations, "retired provider identifiers in data layer: " + "; ".join(violations)


def test_data_layer_never_references_retired_research_skeletons() -> None:
    """Retired experiment skeletons stay out of the completed Phase 1 layer."""
    violations = _data_layer_identifier_violations(RETIRED_RESEARCH_SKELETONS)
    assert not violations, "retired research skeletons in data layer: " + "; ".join(violations)


def test_data_layer_legacy_identifier_guard_detects_each_scoped_name() -> None:
    """The guard recognizes both direct and aliased imports for every name."""
    forbidden = RETIRED_PROVIDER_IDENTIFIERS | RETIRED_RESEARCH_SKELETONS
    for identifier in forbidden:
        tree = ast.parse(f"from trading_research.core import {identifier} as _legacy\n")
        assert _forbidden_identifier_violations(tree, {identifier})


def test_native_primitives_depend_only_on_the_standard_library() -> None:
    """These modules predate every dependency and must keep it that way.

    They are also the layer a later slice would most easily contaminate:
    reaching for an array library here would push a third-party type into the
    platform's most public contracts.
    """
    permitted_internal = {f"{ROOT_NAME}.core.errors"}
    violations: list[str] = []
    for path in _primitive_module_paths():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.append(node.module)
            for module in modules:
                if module.startswith(ROOT_NAME):
                    if module not in permitted_internal:
                        violations.append(f"{path.relative_to(PACKAGE_ROOT)} -> {module}")
                elif module.split(".")[0] not in sys.stdlib_module_names:
                    violations.append(f"{path.relative_to(PACKAGE_ROOT)} -> {module}")
    assert not violations, "native primitives must stay standard-library only: " + "; ".join(
        violations
    )


def test_contract_layers_import_no_dataframe_library() -> None:
    """DataFrame semantics must not reach a public contract (ADR-0003).

    Scoped to the contract-bearing layers. Plugins may use these internally,
    which the ADR explicitly permits, so the ban stops at ``core`` and
    ``data``.
    """
    violations: list[str] = []
    for layer in ("core", "data"):
        for path in _layer_files(layer):
            tree = ast.parse(path.read_text(), filename=str(path))
            forbidden = _imported_module_roots(tree) & FORBIDDEN_FRAME_LIBRARIES
            violations.extend(
                f"{path.relative_to(PACKAGE_ROOT)} -> {module}" for module in sorted(forbidden)
            )
    assert not violations, "contract layers must not import a DataFrame library: " + "; ".join(
        violations
    )


def test_public_contract_annotations_expose_no_table_library_types() -> None:
    """Arrow and DataFrame types may be internal, never part of a public API."""
    violations: list[str] = []
    for layer in ("core", "data"):
        for path in _layer_files(layer):
            tree = ast.parse(path.read_text(), filename=str(path))
            aliases = _symbol_aliases(tree)
            for site, annotation in _public_annotation_sites(tree):
                forbidden = sorted(
                    reference
                    for reference in _expression_references(annotation, aliases)
                    if reference.split(".", maxsplit=1)[0] in FORBIDDEN_PUBLIC_TYPE_LIBRARIES
                )
                if forbidden:
                    violations.append(f"{path.relative_to(PACKAGE_ROOT)} {site} -> {forbidden}")

            # Explicit exports and public type aliases are public even when a
            # function has not used them in an annotation yet.
            public_aliases = _declared_exports(tree)
            public_aliases.update(
                name
                for node in tree.body
                if isinstance(node, ast.Assign | ast.AnnAssign | ast.TypeAlias)
                for name in _assigned_names(node)
                if not name.startswith("_")
            )
            for name in sorted(public_aliases):
                forbidden = sorted(
                    reference
                    for reference in aliases.get(name, set())
                    if reference.split(".", maxsplit=1)[0] in FORBIDDEN_PUBLIC_TYPE_LIBRARIES
                )
                if forbidden:
                    violations.append(
                        f"{path.relative_to(PACKAGE_ROOT)} exports {name} -> {forbidden}"
                    )
    assert not violations, "public contracts expose a table-library type: " + "; ".join(violations)


def test_public_type_guard_deliberately_permits_numpy_arrays() -> None:
    """Read-only NumPy arrays are ADR-0003's intended public column type."""
    tree = ast.parse(
        "import numpy as np\n"
        "class ColumnContract:\n"
        "    def column(self) -> np.ndarray:\n"
        "        raise NotImplementedError\n"
    )
    aliases = _symbol_aliases(tree)
    references = {
        reference
        for _, annotation in _public_annotation_sites(tree)
        for reference in _expression_references(annotation, aliases)
    }
    assert "numpy.ndarray" in references
    assert not {
        reference
        for reference in references
        if reference.split(".", maxsplit=1)[0] in FORBIDDEN_PUBLIC_TYPE_LIBRARIES
    }


def test_market_frame_loader_has_the_exact_public_load_api() -> None:
    """The sole row-loading method has one fixed, grant-bearing signature."""
    path = PACKAGE_ROOT / "data" / "integrity.py"
    assert path.is_file(), "Slice 1D must define data/integrity.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    aliases = _symbol_aliases(tree)
    loader_classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MarketFrameLoader"
    ]
    assert len(loader_classes) == 1, "integrity.py must define exactly one MarketFrameLoader"
    public_methods = {
        member.name
        for member in loader_classes[0].body
        if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
        and not member.name.startswith("_")
    }
    assert public_methods == {"load"}, (
        "MarketFrameLoader public callable surface must be exactly {'load'}; "
        f"observed {sorted(public_methods)}"
    )
    loader_type = importlib.import_module(f"{ROOT_NAME}.data.integrity").MarketFrameLoader
    runtime_public_callables = {
        name
        for name, value in vars(loader_type).items()
        if not name.startswith("_") and callable(value)
    }
    assert runtime_public_callables == {"load"}, (
        "MarketFrameLoader runtime callable surface must be exactly {'load'}; "
        f"observed {sorted(runtime_public_callables)}"
    )
    load_methods = [
        node
        for node in loader_classes[0].body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "load"
    ]
    assert len(load_methods) == 1, "MarketFrameLoader must define exactly one load method"
    load = load_methods[0]
    assert isinstance(load, ast.FunctionDef), "MarketFrameLoader.load must be synchronous"

    assert not load.args.posonlyargs
    assert [argument.arg for argument in load.args.args] == ["self", "grant"]
    assert not load.args.defaults
    assert load.args.vararg is None
    assert load.args.kwarg is None
    assert [argument.arg for argument in load.args.kwonlyargs] == ["columns", "lo", "hi"]
    assert all(
        isinstance(default, ast.Constant) and default.value is None
        for default in load.args.kw_defaults
    ), "columns, lo and hi must each default to None"

    self_argument, grant_argument = load.args.args
    assert self_argument.annotation is None
    assert _annotation_leaf_names(grant_argument.annotation, aliases) == {"AccessGrant"}
    assert grant_argument.annotation is not None
    assert not _annotation_is_optional(grant_argument.annotation, aliases)

    columns_argument, lo_argument, hi_argument = load.args.kwonlyargs
    assert _annotation_leaf_names(columns_argument.annotation, aliases) == {"Sequence", "str"}
    assert columns_argument.annotation is not None
    assert _annotation_is_optional(columns_argument.annotation, aliases)
    for argument in (lo_argument, hi_argument):
        assert _annotation_leaf_names(argument.annotation, aliases) == {"NativeTimeKey"}
        assert argument.annotation is not None
        assert _annotation_is_optional(argument.annotation, aliases)
    assert _annotation_leaf_names(load.returns, aliases) == {"MarketFrame"}

    assert _declared_exports(tree) == {"MarketFrameLoader"}
    package_path = PACKAGE_ROOT / "data" / "__init__.py"
    package_tree = ast.parse(package_path.read_text(), filename=str(package_path))
    package_aliases = _symbol_aliases(package_tree)
    assert "MarketFrameLoader" in _declared_exports(package_tree)
    assert package_aliases.get("MarketFrameLoader") == {
        "trading_research.data.integrity.MarketFrameLoader"
    }


def test_open_bounded_view_has_the_exact_approved_composition_api() -> None:
    """Composition requires explicit infrastructure and exact grant sequence."""
    path = PACKAGE_ROOT / "data" / "views.py"
    assert path.is_file(), "Slice 1E must define data/views.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    assert not _slice_1e_composition_signature_violations(tree)


def test_slice_1e_views_perform_no_direct_io_or_decoding() -> None:
    """Only MarketFrameLoader.load may cross from composition into rows."""
    path = PACKAGE_ROOT / "data" / "views.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    assert not _slice_1e_io_violations(tree)


def test_slice_1e_contains_no_split_policy_date_or_key_constants() -> None:
    """Role adjacency comes from policy contracts, never current V1 bounds."""
    policy_path = PACKAGE_ROOT.parents[1] / "config" / "split-policies" / "adr-0012-v1.json"
    policy = json.loads(policy_path.read_text())
    ranges = policy["ranges"]
    forbidden = {
        value
        for span in ranges.values()
        for value in (span["start_inclusive_key"], span["end_inclusive_key"])
    }
    tree = ast.parse((PACKAGE_ROOT / "data" / "views.py").read_text())
    observed = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    }
    assert not observed & forbidden


def test_bounded_view_contracts_retain_no_loading_authority_or_location() -> None:
    """Returned semantic objects hold only bounded data and value evidence."""
    path = PACKAGE_ROOT / "data" / "views.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    assert not _slice_1e_bound_view_storage_violations(tree)

    module = importlib.import_module(f"{ROOT_NAME}.data.views")
    forbidden_tokens = {"grant", "loader", "manifest", "path", "policy", "registry", "root"}
    for contract_name in ("BoundedSegment", "BoundedView", "ContextResolution"):
        contract = getattr(module, contract_name)
        slots = set(contract.__slots__)
        assert not {
            slot for slot in slots if any(token in slot.lower() for token in forbidden_tokens)
        }
    assert not hasattr(module.BoundedView, "frame")


def test_slice_1e_public_contract_surfaces_are_exact_and_small() -> None:
    """No convenience API may widen bounded exposure or union semantics."""
    path = PACKAGE_ROOT / "data" / "views.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    expected = {
        "BoundedSegment": {"dataset_ref", "role", "frame"},
        "BoundedView": {
            "segments",
            "eval_frame",
            "context",
            "context_start",
            "eval_start",
            "eval_end",
        },
        "ContextResolution": {
            "requested_context_start",
            "resolved_context_start",
            "shortfall",
            "short_context_accepted",
        },
        "ContextShortfall": {
            "requested_context_start",
            "resolved_context_start",
            "reason_code",
            "detail",
            "accepted",
        },
        "ViewUnion": {"members"},
        "ViewUnionMember": {"dataset_ref", "role"},
    }
    observed: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name not in expected:
            continue
        members: set[str] = set()
        for child in node.body:
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                if not child.name.startswith("_"):
                    members.add(child.name)
            elif (
                isinstance(child, ast.AnnAssign)
                and isinstance(child.target, ast.Name)
                and not child.target.id.startswith("_")
            ):
                members.add(child.target.id)
        observed[node.name] = members
    assert observed == expected
    assert _declared_exports(tree) == VIEWS_PUBLIC_CONTRACTS


def test_view_union_storage_contains_no_rows_authority_or_aggregate_identity() -> None:
    """The union is representation/validation only in Slice 1E."""
    module = importlib.import_module(f"{ROOT_NAME}.data.views")
    assert set(module.ViewUnion.__slots__) == {"_members"}
    assert set(module.ViewUnionMember.__slots__) == {
        "_dataset_ref",
        "_split_policy_identity",
    }
    forbidden = {"frame", "grant", "loader", "fingerprint", "hash", "range"}
    public = {
        name
        for contract in (module.ViewUnion, module.ViewUnionMember)
        for name in vars(contract)
        if not name.startswith("_")
    }
    assert not public & forbidden


def test_slice_1e_view_role_handling_is_exhaustive_without_fallback() -> None:
    """A future ViewRole cannot silently inherit context/union behaviour."""
    path = PACKAGE_ROOT / "data" / "views.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    assert not _slice_1e_role_match_violations(tree)


def test_slice_1e_finite_composition_mutations_are_detected() -> None:
    """Closed signature regressions: missing loader, optional grants, bare path, decoder."""
    path = PACKAGE_ROOT / "data" / "views.py"
    source = path.read_text()
    mutations = (
        source.replace("    loader: _MarketFrameLoader,\n", "", 1),
        source.replace(
            "    grants: _Sequence[_AccessGrant],\n",
            "    grants: _Sequence[_AccessGrant] | None = None,\n",
            1,
        ),
        source.replace(
            "    loader: _MarketFrameLoader,\n",
            "    loader: _MarketFrameLoader,\n    path: str,\n",
            1,
        ),
        source.replace("        frame = loader.load(\n", "        frame = _decode_partition(\n", 1),
    )
    assert all(
        _slice_1e_composition_signature_violations(ast.parse(mutated)) for mutated in mutations
    )


def test_slice_1e_finite_io_storage_and_role_mutations_are_detected() -> None:
    """Closed regressions: direct I/O, retained loader, and role fallback."""
    path = PACKAGE_ROOT / "data" / "views.py"
    source = path.read_text()
    direct_io = ast.parse(source + "\nimport pyarrow.parquet\n")
    retained_loader = ast.parse(
        source.replace(
            "    _segments: tuple[BoundedSegment, ...]\n",
            "    _segments: tuple[BoundedSegment, ...]\n    _loader: _MarketFrameLoader\n",
            1,
        )
    )
    fallback_role = ast.parse(
        source.replace(
            "        case _ViewRole.SNAPSHOT:\n            return False\n",
            "        case _:\n            return False\n",
            1,
        )
    )
    assert _slice_1e_io_violations(direct_io)
    assert _slice_1e_bound_view_storage_violations(retained_loader)
    assert _slice_1e_role_match_violations(fallback_role)


def test_governed_slice_1d_public_bindings_are_canonical() -> None:
    """Approved public names retain their exact source origin and runtime object."""
    governed = [
        (
            "core/frames.py",
            f"{ROOT_NAME}.core.frames",
            CORE_FRAME_CANONICAL_BINDINGS,
            CORE_FRAME_RUNTIME_BINDINGS,
            {"MarketFrame"},
        ),
        (
            "core/__init__.py",
            f"{ROOT_NAME}.core",
            CORE_PACKAGE_CANONICAL_BINDINGS,
            CORE_PACKAGE_RUNTIME_BINDINGS,
            set(CORE_PACKAGE_CANONICAL_BINDINGS),
        ),
        (
            "data/integrity.py",
            f"{ROOT_NAME}.data.integrity",
            INTEGRITY_CANONICAL_BINDINGS,
            INTEGRITY_RUNTIME_BINDINGS,
            {"MarketFrameLoader"},
        ),
        (
            "data/views.py",
            f"{ROOT_NAME}.data.views",
            VIEWS_CANONICAL_BINDINGS,
            VIEWS_RUNTIME_BINDINGS,
            VIEWS_PUBLIC_CONTRACTS,
        ),
        (
            "data/gaps.py",
            f"{ROOT_NAME}.data.gaps",
            GAPS_CANONICAL_BINDINGS,
            GAPS_RUNTIME_BINDINGS,
            {"GapIndex"},
        ),
        (
            "data/__init__.py",
            f"{ROOT_NAME}.data",
            DATA_PACKAGE_CANONICAL_BINDINGS,
            _expected_data_package_runtime_bindings(),
            set(DATA_PACKAGE_CANONICAL_BINDINGS),
        ),
    ]
    adapter_root = PACKAGE_ROOT / "data" / "adapters"
    for path in sorted(adapter_root.rglob("*.py")):
        relative = path.relative_to(PACKAGE_ROOT).as_posix()
        relative_module = path.relative_to(PACKAGE_ROOT).with_suffix("")
        module_parts = relative_module.parts
        if module_parts[-1] == "__init__":
            module_parts = module_parts[:-1]
        governed.append(
            (
                relative,
                ".".join((ROOT_NAME, *module_parts)),
                ADAPTER_CANONICAL_SOURCE_BINDINGS.get(relative, {}),
                ADAPTER_CANONICAL_RUNTIME_BINDINGS.get(relative, {}),
                set(),
            )
        )

    violations: list[str] = []
    for relative, module_name, source_expected, runtime_expected, exports in governed:
        path = PACKAGE_ROOT / relative
        tree = ast.parse(path.read_text(), filename=str(path))
        if _declared_exports(tree) != exports:
            violations.append(
                f"{relative} exports differ: expected {sorted(exports)}, "
                f"observed {sorted(_declared_exports(tree))}"
            )
        violations.extend(
            f"{relative}: {detail}"
            for detail in _canonical_source_binding_violations(tree, source_expected)
        )
        violations.extend(
            f"{relative}: {detail}"
            for detail in _canonical_runtime_binding_violations(module_name, runtime_expected)
        )
    assert not violations, "governed Slice 1D binding drift: " + "; ".join(violations)


def test_frame_support_name_rebinding_regression_is_detected() -> None:
    """An allowlisted frame support name cannot hide constructor machinery."""
    path = PACKAGE_ROOT / "core" / "frames.py"
    mutated = ast.parse(
        path.read_text()
        + "\nimport functools as _functools\n"
        + "Any = _functools.partial(_make_market_frame)\n",
        filename=str(path),
    )
    violations = _canonical_source_binding_violations(mutated, CORE_FRAME_CANONICAL_BINDINGS)
    assert any("Any origin differs" in violation for violation in violations)


def test_integrity_support_name_rebinding_regression_is_detected() -> None:
    """An allowlisted integrity support name cannot hide a loader delegate."""
    path = PACKAGE_ROOT / "data" / "integrity.py"
    mutated = ast.parse(
        path.read_text()
        + "\nimport functools as _functools\n"
        + "def _delegate(loader, grant):\n"
        + "    return loader.load(grant)\n"
        + "dataclass = _functools.partial(_delegate)\n",
        filename=str(path),
    )
    violations = _canonical_source_binding_violations(mutated, INTEGRITY_CANONICAL_BINDINGS)
    assert any("dataclass origin differs" in violation for violation in violations)


def test_governed_slice_1d_modules_define_no_dynamic_public_attributes() -> None:
    """Module-level dynamic attributes are outside the finite public surface."""
    paths = [PACKAGE_ROOT / relative for relative in GOVERNED_DYNAMIC_ATTRIBUTE_MODULES]
    paths.extend(sorted((PACKAGE_ROOT / "data" / "adapters").rglob("*.py")))
    violations: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        hooks = sorted(_dynamic_public_attribute_definitions(tree))
        if hooks:
            violations.append(f"{path.relative_to(PACKAGE_ROOT)} defines {hooks}")
    assert not violations, "dynamic Slice 1D module attributes: " + "; ".join(violations)


def test_dynamic_public_attribute_regression_is_detected() -> None:
    """A synthetic module-level __getattr__ bypass is recognized directly."""
    tree = ast.parse(
        "def __getattr__(name):\n"
        "    if name == 'issue':\n"
        "        return _make_market_frame\n"
        "    raise AttributeError(name)\n"
    )
    assert _dynamic_public_attribute_definitions(tree) == {"__getattr__"}


def test_integrity_module_has_an_exact_public_surface() -> None:
    """No innocuous integrity-module wrapper may become a second row route."""
    integrity_path = PACKAGE_ROOT / "data" / "integrity.py"
    integrity_tree = ast.parse(integrity_path.read_text(), filename=str(integrity_path))
    assert _declared_exports(integrity_tree) == {"MarketFrameLoader"}
    assert _module_level_public_names(integrity_tree) == INTEGRITY_MODULE_PUBLIC_BINDINGS

    integrity_module = importlib.import_module(f"{ROOT_NAME}.data.integrity")
    assert set(_runtime_public_bindings(integrity_module)) == INTEGRITY_MODULE_PUBLIC_BINDINGS


def test_data_package_has_an_exact_public_surface() -> None:
    """The data package exports only its approved catalog/gate/loader contracts."""
    package_path = PACKAGE_ROOT / "data" / "__init__.py"
    package_tree = ast.parse(package_path.read_text(), filename=str(package_path))
    assert _declared_exports(package_tree) == DATA_PACKAGE_PUBLIC_EXPORTS
    assert _module_level_public_names(package_tree) == DATA_PACKAGE_PUBLIC_EXPORTS
    assert not _module_owned_public_definitions(package_tree)

    package_module = importlib.import_module(f"{ROOT_NAME}.data")
    assert set(_runtime_public_bindings(package_module)) == set(
        _expected_data_package_runtime_bindings()
    )
    integrity_module = importlib.import_module(f"{ROOT_NAME}.data.integrity")
    assert package_module.MarketFrameLoader is integrity_module.MarketFrameLoader


def test_public_row_loaders_require_an_exact_access_grant_boundary() -> None:
    """Distinguish the sole decoder, composition route, and bounded accessors."""
    violations: list[str] = []
    decoder_count = 0
    composition_count = 0
    for path in _slice_1d_paths():
        relative = path.relative_to(PACKAGE_ROOT).as_posix()
        tree = ast.parse(path.read_text(), filename=str(path))
        aliases = _symbol_aliases(tree)
        for qualified_name, node in _public_callable_nodes(tree):
            if not _looks_like_row_loader(qualified_name, node, aliases):
                continue
            route = (relative, qualified_name)
            if route in APPROVED_BOUNDED_FRAME_ACCESSORS:
                continue
            if route == APPROVED_PUBLIC_DECODER_ROUTE:
                decoder_count += 1
            elif route == APPROVED_PUBLIC_COMPOSITION_ROUTE:
                composition_count += 1
            else:
                violations.append(f"{relative} exposes unapproved row route {qualified_name}")
                continue

            label = f"{path.relative_to(PACKAGE_ROOT)}:{qualified_name}"
            parameters = _parameter_records(node)
            grants = [
                (argument, has_default)
                for argument, has_default, _ in parameters
                if _mentions_type(argument.annotation, aliases, "AccessGrant")
            ]
            if not grants:
                violations.append(f"{label} has no AccessGrant parameter")
            elif len(grants) != 1:
                violations.append(
                    f"{label} must accept exactly one AccessGrant parameter, found {len(grants)}"
                )
            for argument, has_default in grants:
                if has_default or (
                    argument.annotation is not None
                    and _annotation_is_optional(argument.annotation, aliases)
                ):
                    violations.append(
                        f"{label} makes AccessGrant parameter {argument.arg!r} optional"
                    )

            forbidden_parameter_types = (
                FORBIDDEN_LOADER_PARAMETER_TYPES
                if route == APPROVED_PUBLIC_DECODER_ROUTE
                else FORBIDDEN_COMPOSITION_PARAMETER_TYPES
            )
            for argument, _, _ in parameters:
                if argument.arg in {"self", "cls"}:
                    continue
                references = (
                    _expression_references(argument.annotation, aliases)
                    if argument.annotation is not None
                    else set()
                )
                forbidden_types = sorted(
                    reference
                    for reference in references
                    if _reference_leaf(reference) in forbidden_parameter_types
                )
                if forbidden_types:
                    violations.append(
                        f"{label} accepts {argument.arg!r} as bare target/location "
                        f"{forbidden_types}"
                    )
                if _loader_parameter_name_is_forbidden(argument.arg):
                    violations.append(
                        f"{label} exposes forbidden location/bypass parameter {argument.arg!r}"
                    )
            if node.args.vararg is not None or node.args.kwarg is not None:
                violations.append(f"{label} exposes a variadic loading bypass")

    if decoder_count != 1:
        violations.append(f"expected one public decoder, observed {decoder_count}")
    if composition_count != 1:
        violations.append(
            f"expected one approved bounded composition route, observed {composition_count}"
        )
    assert not violations, "public row-loader boundary violations: " + "; ".join(violations)


def test_data_package_exposes_no_alternate_row_or_decoder_route() -> None:
    """Enumerate the sole decoder, composition route, and bounded accessors."""
    observed_routes: set[tuple[str, str]] = set()
    violations: list[str] = []
    for path in _slice_1d_paths():
        relative = path.relative_to(PACKAGE_ROOT).as_posix()
        tree = ast.parse(path.read_text(), filename=str(path))
        aliases = _symbol_aliases(tree)
        for qualified_name, node in _public_callable_nodes(tree):
            if not _looks_like_row_loader(qualified_name, node, aliases):
                continue
            route = (relative, qualified_name)
            observed_routes.add(route)
            if route not in {
                APPROVED_PUBLIC_DECODER_ROUTE,
                APPROVED_PUBLIC_COMPOSITION_ROUTE,
                *APPROVED_BOUNDED_FRAME_ACCESSORS,
            }:
                violations.append(f"{relative} exposes alternate row route {qualified_name}")

        for name, references in _public_route_bindings(tree, aliases):
            allowed = (relative, name) == APPROVED_LOADER_EXPORT[:2] and references == {
                APPROVED_LOADER_EXPORT[2]
            }
            if not allowed:
                violations.append(
                    f"{relative} exposes row/decoder binding {name} -> {sorted(references)}"
                )

    assert APPROVED_PUBLIC_DECODER_ROUTE in observed_routes, (
        "the approved MarketFrameLoader.load route was not found"
    )
    assert APPROVED_PUBLIC_COMPOSITION_ROUTE in observed_routes, (
        "the approved open_bounded_view composition route was not found"
    )
    assert observed_routes >= APPROVED_BOUNDED_FRAME_ACCESSORS
    assert not violations, "alternate public row-loading routes: " + "; ".join(violations)


def test_slice_1d_loader_never_reauthorizes_or_rereads_a_manifest() -> None:
    """A loader consumes a grant; it never recreates the gate or its inputs."""
    violations: list[str] = []
    for path in _loader_implementation_paths():
        tree = ast.parse(path.read_text(), filename=str(path))
        aliases = _symbol_aliases(tree)
        for local, references in aliases.items():
            for reference in references:
                leaf = _reference_leaf(reference)
                if leaf in FORBIDDEN_LOADER_OPERATIONS:
                    violations.append(
                        f"{path.relative_to(PACKAGE_ROOT)} imports {reference} as {local}"
                    )
                if ".data.audit" in reference:
                    violations.append(
                        f"{path.relative_to(PACKAGE_ROOT)} imports audit machinery {reference}"
                    )

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "_issue":
                violations.append(
                    f"{path.relative_to(PACKAGE_ROOT)} calls or references a grant issuer"
                )
            if not isinstance(node, ast.Call):
                continue
            references = _expression_references(node.func, aliases)
            forbidden_calls = sorted(
                reference
                for reference in references
                if _reference_leaf(reference) in FORBIDDEN_LOADER_OPERATIONS
            )
            if forbidden_calls:
                violations.append(f"{path.relative_to(PACKAGE_ROOT)} calls {forbidden_calls}")
            if any(_reference_leaf(reference) == "AccessGrant" for reference in references):
                violations.append(
                    f"{path.relative_to(PACKAGE_ROOT)} constructs AccessGrant directly"
                )
    assert not violations, "Slice 1D loader recreates authorization: " + "; ".join(violations)


def test_adapters_have_an_exact_empty_public_surface_and_export_policy() -> None:
    """Adapters expose neither row loading nor physical schema discovery."""
    adapter_root = PACKAGE_ROOT / "data" / "adapters"
    if not adapter_root.is_dir():
        return
    violations: list[str] = []
    for path in sorted(adapter_root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        exports = _declared_exports(tree)
        if exports:
            violations.append(f"{path.relative_to(PACKAGE_ROOT)} exports {sorted(exports)}")

        relative_module = path.relative_to(PACKAGE_ROOT).with_suffix("")
        module_parts = relative_module.parts
        if module_parts[-1] == "__init__":
            module_parts = module_parts[:-1]
        module_name = ".".join((ROOT_NAME, *module_parts))
        module = importlib.import_module(module_name)
        public_names = {name for name in vars(module) if not name.startswith("_")}
        relative = path.relative_to(PACKAGE_ROOT).as_posix()
        expected_names = ADAPTER_RUNTIME_PUBLIC_ALLOWLIST.get(relative, set())
        if public_names != expected_names:
            violations.append(
                f"{path.relative_to(PACKAGE_ROOT)} binds public names "
                f"{sorted(public_names)}; expected {sorted(expected_names)}"
            )
    assert not violations, "adapter public-surface violations: " + "; ".join(violations)


def test_market_frame_has_the_exact_closed_public_surface() -> None:
    """No public method or module-owned factory may bypass frame validation."""
    path = PACKAGE_ROOT / "core" / "frames.py"
    if not path.is_file():
        return
    tree = ast.parse(path.read_text(), filename=str(path))
    market_frames = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MarketFrame"
    ]
    assert len(market_frames) == 1, "core/frames.py must define exactly one MarketFrame"

    frame_node = market_frames[0]
    public_members: set[str] = set()
    for member in frame_node.body:
        if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
            if not member.name.startswith("_"):
                public_members.add(member.name)
        elif (
            isinstance(member, ast.AnnAssign)
            and isinstance(member.target, ast.Name)
            and not member.target.id.startswith("_")
        ):
            public_members.add(member.target.id)
    assert public_members == MARKET_FRAME_PUBLIC_SURFACE, (
        "MarketFrame public surface changed; expected "
        f"{sorted(MARKET_FRAME_PUBLIC_SURFACE)}, observed {sorted(public_members)}"
    )
    assert _declared_exports(tree) == {"MarketFrame"}
    assert _module_level_public_names(tree) == CORE_FRAME_MODULE_PUBLIC_BINDINGS

    module = importlib.import_module(f"{ROOT_NAME}.core.frames")
    assert set(_runtime_public_bindings(module)) == CORE_FRAME_MODULE_PUBLIC_BINDINGS
    assert isinstance(module.MarketFrame, type)


def test_core_package_exposes_only_the_approved_market_frame_symbol() -> None:
    """Private frame machinery cannot become public through package aliasing."""
    package_path = PACKAGE_ROOT / "core" / "__init__.py"
    tree = ast.parse(package_path.read_text(), filename=str(package_path))
    aliases = _symbol_aliases(tree)
    exports = _declared_exports(tree)
    frame_bindings = {
        name: references
        for name in _module_level_bound_names(tree)
        if _is_public_name(name, exports)
        and (references := aliases.get(name, set()))
        and any(reference.startswith(f"{ROOT_NAME}.core.frames.") for reference in references)
    }
    assert frame_bindings == CORE_FRAME_PUBLIC_BINDINGS

    package_module = importlib.import_module(f"{ROOT_NAME}.core")
    frame_module = importlib.import_module(f"{ROOT_NAME}.core.frames")
    assert package_module.MarketFrame is frame_module.MarketFrame


# ------------------------------------------------------ Slice 2A feature contracts


def _class_public_surface(node: ast.ClassDef) -> set[str]:
    """Return public methods, properties and annotated fields of one class."""
    surface: set[str] = set()
    for member in node.body:
        if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
            if not member.name.startswith("_"):
                surface.add(member.name)
        elif (
            isinstance(member, ast.AnnAssign)
            and isinstance(member.target, ast.Name)
            and not member.target.id.startswith("_")
        ):
            surface.add(member.target.id)
    return surface


def _only_class(tree: ast.Module, name: str) -> ast.ClassDef:
    """Return the sole top-level class named ``name`` in one module."""
    matches = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name]
    assert len(matches) == 1, f"expected exactly one {name}, found {len(matches)}"
    return matches[0]


def _slice_2d_identity_tree() -> ast.Module:
    """Parse the pure Slice 2D identity implementation."""
    path = PACKAGE_ROOT / "artifacts" / "identity.py"
    return ast.parse(path.read_text(), filename=str(path))


def _slice_2d_dataclass_options(
    node: ast.ClassDef,
    aliases: dict[str, set[str]],
) -> list[dict[str, object]]:
    """Return literal keyword options from this class's dataclass decorator."""
    options: list[dict[str, object]] = []
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        references = _expression_references(decorator.func, aliases)
        if not any(reference == "dataclasses.dataclass" for reference in references):
            continue
        if decorator.args or any(keyword.arg is None for keyword in decorator.keywords):
            options.append({"<invalid-call-shape>": None})
            continue
        values: dict[str, object] = {}
        for keyword in decorator.keywords:
            if keyword.arg is None or not isinstance(keyword.value, ast.Constant):
                values["<nonliteral-option>"] = None
                continue
            values[keyword.arg] = keyword.value.value
        options.append(values)
    return options


def _slice_2d_class_contract_violations(tree: ast.Module) -> list[str]:
    """Pin the two public value types and StageIdentity's sole entry point."""
    aliases = _symbol_aliases(tree)
    violations: list[str] = []
    classes = {
        name: [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name]
        for name in SLICE_2D_PUBLIC_EXPORTS
    }
    for name, matches in classes.items():
        if len(matches) != 1:
            violations.append(f"expected exactly one {name}, found {len(matches)}")
    if any(len(matches) != 1 for matches in classes.values()):
        return violations

    platform = classes["PlatformIdentity"][0]
    stage = classes["StageIdentity"][0]
    if _class_public_surface(platform) != {
        "diff_sha256",
        "git_commit",
        "git_dirty",
    }:
        violations.append(
            f"PlatformIdentity public fields differ: {sorted(_class_public_surface(platform))}"
        )
    platform_fields = {
        member.target.id: member.annotation
        for member in platform.body
        if isinstance(member, ast.AnnAssign)
        and isinstance(member.target, ast.Name)
        and not member.target.id.startswith("_")
    }
    expected_platform_types = {
        "git_commit": {"str"},
        "git_dirty": {"bool"},
        "diff_sha256": {"str"},
    }
    for name, expected in expected_platform_types.items():
        annotation = platform_fields.get(name)
        observed = (
            {
                _reference_leaf(reference)
                for reference in _expression_references(annotation, aliases)
            }
            if annotation is not None
            else set()
        )
        if observed != expected:
            violations.append(f"PlatformIdentity.{name} annotation differs: {sorted(observed)}")
        optional = annotation is not None and _annotation_is_optional(annotation, aliases)
        if optional != (name == "diff_sha256"):
            violations.append(f"PlatformIdentity.{name} optionality differs: {optional}")
    if _slice_2d_dataclass_options(platform, aliases) != [{"frozen": True, "slots": True}]:
        violations.append("PlatformIdentity must be exactly a frozen slotted dataclass")

    if _class_public_surface(stage) != {"identity_key"}:
        violations.append(
            f"StageIdentity public surface differs: {sorted(_class_public_surface(stage))}"
        )
    if _slice_2d_dataclass_options(stage, aliases) != [
        {"frozen": True, "slots": True, "init": False}
    ]:
        violations.append(
            "StageIdentity must be exactly a frozen slotted dataclass with init=False"
        )

    initializers = [
        member
        for member in stage.body
        if isinstance(member, ast.FunctionDef) and member.name == "__init__"
    ]
    if len(initializers) != 1:
        violations.append(
            f"StageIdentity must define exactly one __init__, found {len(initializers)}"
        )
    else:
        initializer = initializers[0]
        expected_keywords = [
            "source_identities",
            "resolved_context_start",
            "eval_start",
            "eval_end",
            "split_policy_adr",
            "split_policy_version",
            "split_policy_sha256",
            "providers",
            "platform_identity",
        ]
        if (
            initializer.args.posonlyargs
            or [argument.arg for argument in initializer.args.args] != ["self"]
            or initializer.args.defaults
            or [argument.arg for argument in initializer.args.kwonlyargs] != expected_keywords
            or any(default is not None for default in initializer.args.kw_defaults)
            or initializer.args.vararg is not None
            or initializer.args.kwarg is not None
        ):
            violations.append(
                "StageIdentity.__init__ must have the exact required keyword-only signature"
            )
        expected_annotations = {
            "source_identities": {
                "collections.abc.Sequence",
                f"{ROOT_NAME}.core.identity.DatasetIdentity",
            },
            "resolved_context_start": {f"{ROOT_NAME}.core.time.NativeTimeKey"},
            "eval_start": {f"{ROOT_NAME}.core.time.NativeTimeKey"},
            "eval_end": {f"{ROOT_NAME}.core.time.NativeTimeKey"},
            "split_policy_adr": {"str"},
            "split_policy_version": {"str"},
            "split_policy_sha256": {"str"},
            "providers": {
                "collections.abc.Sequence",
                f"{ROOT_NAME}.core.interfaces.FeatureProvider",
            },
            "platform_identity": {"PlatformIdentity"},
        }
        for argument in initializer.args.kwonlyargs:
            observed = (
                _expression_references(argument.annotation, aliases)
                if argument.annotation is not None
                else set()
            )
            expected = expected_annotations.get(argument.arg, set())
            if observed != expected:
                violations.append(
                    f"StageIdentity.__init__ {argument.arg} annotation differs: {sorted(observed)}"
                )
        if (
            not isinstance(initializer.returns, ast.Constant)
            or initializer.returns.value is not None
        ):
            violations.append("StageIdentity.__init__ must return None")

    identity_methods = [
        member
        for member in stage.body
        if isinstance(member, ast.FunctionDef) and member.name == "identity_key"
    ]
    if len(identity_methods) != 1:
        violations.append(
            f"StageIdentity must define exactly one identity_key, found {len(identity_methods)}"
        )
    else:
        method = identity_methods[0]
        return_references = (
            _expression_references(method.returns, aliases) if method.returns is not None else set()
        )
        if (
            method.args.posonlyargs
            or [argument.arg for argument in method.args.args] != ["self"]
            or method.args.defaults
            or method.args.kwonlyargs
            or method.args.vararg is not None
            or method.args.kwarg is not None
            or return_references != {"str"}
        ):
            violations.append("identity_key must accept only self and return str")
        returns = [node for node in ast.walk(method) if isinstance(node, ast.Return)]
        if (
            len(returns) != 1
            or not isinstance(returns[0].value, ast.Attribute)
            or not isinstance(returns[0].value.value, ast.Name)
            or returns[0].value.value.id != "self"
            or returns[0].value.attr != "_identity_key"
        ):
            violations.append("identity_key must return the precomputed private key directly")
    return violations


def _slice_2d_purity_violations(tree: ast.Module) -> list[str]:
    """Reject every import and bounded ambient/I/O/hash route outside pure identity."""
    violations: set[str] = set()
    imports = _absolute_import_modules(tree)
    if imports != SLICE_2D_IDENTITY_IMPORTS:
        violations.add(
            "imports differ: "
            f"expected {sorted(SLICE_2D_IDENTITY_IMPORTS)}, observed {sorted(imports)}"
        )
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            violations.add("uses a relative import")
        if isinstance(node, ast.Import | ast.ImportFrom) and any(
            imported.name == "*" for imported in node.names
        ):
            violations.add("uses a wildcard import")

    aliases = _symbol_aliases(tree)
    for node in ast.walk(tree):
        qualified = _qualified_name(node)
        if qualified is not None:
            references = _resolve_name(qualified, aliases)
            forbidden = sorted(
                reference
                for reference in references
                if _reference_leaf(reference) in SLICE_2D_FORBIDDEN_AMBIENT_REFERENCES
            )
            violations.update(f"references ambient {reference}" for reference in forbidden)
        if not isinstance(node, ast.Call):
            continue
        called_references = _expression_references(node.func, aliases)
        called = {_reference_leaf(reference) for reference in called_references}
        for name in sorted(called & SLICE_2D_FORBIDDEN_PURITY_CALLS):
            if name == "compile" and "re.compile" in called_references:
                continue
            violations.add(f"calls forbidden capability {name}")
        for name in sorted(called & SLICE_2D_FORBIDDEN_HASH_CALLS):
            violations.add(f"calls forbidden hash {name}")
    return sorted(violations)


def _slice_2d_encoding_violations(tree: ast.Module) -> list[str]:
    """Pin canonical parameter embedding, JSON options and SHA-256 chaining."""
    aliases = _symbol_aliases(tree)
    violations: list[str] = []
    stage_matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "StageIdentity"
    ]
    if len(stage_matches) != 1:
        return [f"expected exactly one StageIdentity, found {len(stage_matches)}"]
    initializers = [
        member
        for member in stage_matches[0].body
        if isinstance(member, ast.FunctionDef) and member.name == "__init__"
    ]
    if len(initializers) != 1:
        return [f"expected exactly one StageIdentity.__init__, found {len(initializers)}"]
    initializer = initializers[0]

    dumps_calls = [
        node
        for node in ast.walk(initializer)
        if isinstance(node, ast.Call) and "json.dumps" in _expression_references(node.func, aliases)
    ]
    if len(dumps_calls) != 1:
        violations.append(f"expected exactly one json.dumps, found {len(dumps_calls)}")
    else:
        dumps = dumps_calls[0]
        keywords = {keyword.arg: keyword.value for keyword in dumps.keywords}
        separators = keywords.get("separators")
        separator_values = (
            [item.value if isinstance(item, ast.Constant) else object() for item in separators.elts]
            if isinstance(separators, ast.Tuple)
            else []
        )
        exact_separators = (
            isinstance(separators, ast.Tuple)
            and len(separators.elts) == 2
            and separator_values == [",", ":"]
        )
        sort_keys = keywords.get("sort_keys")
        ensure_ascii = keywords.get("ensure_ascii")
        if (
            len(dumps.args) != 1
            or not isinstance(dumps.args[0], ast.Name)
            or dumps.args[0].id != "document"
            or set(keywords) != {"ensure_ascii", "separators", "sort_keys"}
            or not isinstance(sort_keys, ast.Constant)
            or sort_keys.value is not True
            or not isinstance(ensure_ascii, ast.Constant)
            or ensure_ascii.value is not True
            or not exact_separators
        ):
            violations.append("json.dumps must use the exact canonical document options")

    sha_calls: list[ast.Call] = []
    for node in ast.walk(initializer):
        if not isinstance(node, ast.Call):
            continue
        qualified = _qualified_name(node.func)
        references = _resolve_name(qualified, aliases) if qualified is not None else set()
        if "hashlib.sha256" in references:
            sha_calls.append(node)
    if len(sha_calls) != 1:
        violations.append(f"expected exactly one hashlib.sha256, found {len(sha_calls)}")
    else:
        sha = sha_calls[0]
        encoded = sha.args[0] if len(sha.args) == 1 and not sha.keywords else None
        if (
            not isinstance(encoded, ast.Call)
            or not isinstance(encoded.func, ast.Attribute)
            or encoded.func.attr != "encode"
            or not isinstance(encoded.func.value, ast.Name)
            or encoded.func.value.id != "canonical"
            or len(encoded.args) != 1
            or not isinstance(encoded.args[0], ast.Constant)
            or encoded.args[0].value != "utf-8"
            or encoded.keywords
        ):
            violations.append("SHA-256 must consume canonical.encode('utf-8') exactly")
        digest_calls = [
            node
            for node in ast.walk(initializer)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "hexdigest"
            and node.func.value is sha
            and not node.args
            and not node.keywords
        ]
        if len(digest_calls) != 1:
            violations.append("the exact SHA-256 call must feed one hexdigest()")
        else:
            assignments = [
                node
                for node in ast.walk(initializer)
                if isinstance(node, ast.Call)
                and _qualified_name(node.func) == "object.__setattr__"
                and len(node.args) == 3
                and not node.keywords
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "self"
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "_identity_key"
                and node.args[2] is digest_calls[0]
            ]
            if len(assignments) != 1:
                violations.append("the SHA-256 hexdigest must set _identity_key exactly once")

    loads_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and "json.loads" in _expression_references(node.func, aliases)
    ]
    if len(loads_calls) != 1:
        violations.append(f"expected exactly one json.loads, found {len(loads_calls)}")
    else:
        loads = loads_calls[0]
        parameter_call = loads.args[0] if len(loads.args) == 1 and not loads.keywords else None
        if (
            not isinstance(parameter_call, ast.Call)
            or _expression_references(parameter_call.func, aliases)
            != {f"{ROOT_NAME}.core.features.canonical_params_json"}
            or len(parameter_call.args) != 1
            or parameter_call.keywords
            or not isinstance(parameter_call.args[0], ast.Attribute)
            or not isinstance(parameter_call.args[0].value, ast.Name)
            or parameter_call.args[0].value.id != "origin"
            or parameter_call.args[0].attr != "params"
        ):
            violations.append("provider params must embed canonical_params_json(origin.params)")
    return violations


def _slice_2e_module_tree(name: str) -> ast.Module:
    """Parse one production module added or extended by Slice 2E."""
    path = PACKAGE_ROOT / "artifacts" / name
    return ast.parse(path.read_text(), filename=str(path))


def _slice_2e_function(
    tree: ast.Module,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return one exact module-level function, or ``None`` on surface drift."""
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    ]
    return matches[0] if len(matches) == 1 else None


def _slice_2e_literal_assignment(tree: ast.Module, name: str) -> object:
    """Return one literal module-level constant, or a sentinel-like ``None``."""
    matches = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign | ast.AnnAssign)
        and name in _assigned_names(node)
        and node.value is not None
    ]
    if len(matches) != 1:
        return None
    try:
        return ast.literal_eval(matches[0])
    except (TypeError, ValueError):
        return None


def _slice_2e_direct_call_references(
    node: ast.Call,
    aliases: dict[str, set[str]],
) -> set[str]:
    """Resolve only the callable itself, excluding calls nested in its receiver."""
    qualified = _qualified_name(node.func)
    return _resolve_name(qualified, aliases) if qualified is not None else set()


def _slice_2e_identity_snapshot_violations(tree: ast.Module) -> list[str]:
    """Keep the canonical identity preimage private, immutable and key-coupled."""
    aliases = _symbol_aliases(tree)
    stages = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "StageIdentity"
    ]
    if len(stages) != 1:
        return [f"expected one StageIdentity, observed {len(stages)}"]
    stage = stages[0]
    fields = {
        member.target.id: _expression_references(member.annotation, aliases)
        for member in stage.body
        if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name)
    }
    violations: list[str] = []
    if fields != {
        "_canonical_json": {"str"},
        "_identity_key": {"str"},
    }:
        violations.append(f"StageIdentity private fields differ: {fields}")

    field_nodes = {
        member.target.id: member
        for member in stage.body
        if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name)
    }
    canonical_field = field_nodes.get("_canonical_json")
    identity_key_field = field_nodes.get("_identity_key")
    if canonical_field is None or not isinstance(canonical_field.value, ast.Call):
        violations.append("canonical JSON must use one private dataclass field")
    else:
        field_call = canonical_field.value
        field_keywords = {keyword.arg: keyword.value for keyword in field_call.keywords}
        if (
            _expression_references(field_call.func, aliases) != {"dataclasses.field"}
            or field_call.args
            or set(field_keywords) != {"compare", "repr"}
            or not isinstance(field_keywords["repr"], ast.Constant)
            or field_keywords["repr"].value is not False
            or not isinstance(field_keywords["compare"], ast.Constant)
            or field_keywords["compare"].value is not False
        ):
            violations.append("canonical JSON field must be excluded from repr and equality")
    if identity_key_field is None or identity_key_field.value is not None:
        violations.append("identity key must remain the sole comparable dataclass field")

    accessors = [
        member
        for member in stage.body
        if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
        and member.name == "_canonical_document_json"
    ]
    if len(accessors) != 1:
        violations.append(
            "StageIdentity must define exactly one private canonical-document accessor"
        )
    else:
        accessor = accessors[0]
        returns = [node for node in ast.walk(accessor) if isinstance(node, ast.Return)]
        if (
            not isinstance(accessor, ast.FunctionDef)
            or accessor.args.posonlyargs
            or [argument.arg for argument in accessor.args.args] != ["self"]
            or accessor.args.defaults
            or accessor.args.kwonlyargs
            or accessor.args.vararg is not None
            or accessor.args.kwarg is not None
            or (
                _expression_references(accessor.returns, aliases)
                if accessor.returns is not None
                else set()
            )
            != {"str"}
            or len(returns) != 1
            or not isinstance(returns[0].value, ast.Attribute)
            or not isinstance(returns[0].value.value, ast.Name)
            or returns[0].value.value.id != "self"
            or returns[0].value.attr != "_canonical_json"
        ):
            violations.append(
                "private canonical-document accessor must return self._canonical_json"
            )

    initializers = [
        member
        for member in stage.body
        if isinstance(member, ast.FunctionDef) and member.name == "__init__"
    ]
    if len(initializers) != 1:
        violations.append("StageIdentity must retain one exact initializer")
        return violations
    initializer = initializers[0]
    assignments = [
        node
        for node in ast.walk(initializer)
        if isinstance(node, ast.Call)
        and _qualified_name(node.func) == "object.__setattr__"
        and len(node.args) == 3
        and not node.keywords
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "self"
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "_canonical_json"
        and isinstance(node.args[2], ast.Name)
        and node.args[2].id == "canonical"
    ]
    if len(assignments) != 1:
        violations.append("canonical JSON must be snapshotted exactly once from canonical")
    return violations


def _slice_2e_store_api_violations(tree: ast.Module) -> list[str]:
    """Pin the store to the three-call MVP surface and exact byte contract."""
    aliases = _symbol_aliases(tree)
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ArtifactStore"
    ]
    if len(classes) != 1:
        return [f"expected one ArtifactStore, observed {len(classes)}"]
    store = classes[0]
    violations: list[str] = []
    if _class_public_surface(store) != {"get", "put"}:
        violations.append(
            f"ArtifactStore public surface differs: {sorted(_class_public_surface(store))}"
        )

    slots = [
        member.value
        for member in store.body
        if isinstance(member, ast.Assign | ast.AnnAssign)
        and "__slots__" in _assigned_names(member)
        and member.value is not None
    ]
    if len(slots) != 1:
        violations.append("ArtifactStore must declare one exact __slots__")
    else:
        try:
            slot_value = ast.literal_eval(slots[0])
        except (TypeError, ValueError):
            slot_value = None
        if slot_value != ("_runs_root",):
            violations.append(f"ArtifactStore slots differ: {slot_value!r}")

    methods = {
        name: [
            member
            for member in store.body
            if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef) and member.name == name
        ]
        for name in ("__init__", "get", "put")
    }
    if any(len(matches) != 1 for matches in methods.values()):
        violations.append("ArtifactStore must define exactly __init__, get and put once each")
        return violations

    expected: dict[
        str,
        tuple[list[str], list[str], dict[str, set[str]], set[str], bool],
    ] = {
        "__init__": (
            ["self", "runs_root"],
            [],
            {"runs_root": {"pathlib.Path"}},
            set(),
            False,
        ),
        "get": (
            ["self", "identity_key"],
            [],
            {"identity_key": {"str"}},
            {"bytes"},
            True,
        ),
        "put": (
            ["self", "identity_key", "payload"],
            [],
            {"identity_key": {"str"}, "payload": {"bytes"}},
            {"str"},
            False,
        ),
    }
    for name, matches in methods.items():
        method = matches[0]
        positional, keyword_only, expected_annotations, returns, optional = expected[name]
        observed_annotations = {
            argument.arg: _expression_references(argument.annotation, aliases)
            for argument in [*method.args.args, *method.args.kwonlyargs]
            if argument.arg != "self" and argument.annotation is not None
        }
        return_references = (
            _expression_references(method.returns, aliases) if method.returns is not None else set()
        )
        return_optional = method.returns is not None and _annotation_is_optional(
            method.returns, aliases
        )
        return_is_none = isinstance(method.returns, ast.Constant) and method.returns.value is None
        if (
            not isinstance(method, ast.FunctionDef)
            or method.args.posonlyargs
            or [argument.arg for argument in method.args.args] != positional
            or [argument.arg for argument in method.args.kwonlyargs] != keyword_only
            or method.args.defaults
            or any(default is not None for default in method.args.kw_defaults)
            or method.args.vararg is not None
            or method.args.kwarg is not None
            or observed_annotations != expected_annotations
            or (not return_is_none if name == "__init__" else return_references != returns)
            or (name != "__init__" and return_optional != optional)
        ):
            violations.append(f"ArtifactStore.{name} signature differs")
    return violations


def _slice_2e_container_api_violations(tree: ast.Module) -> list[str]:
    """Keep serialization internal and pin its two bounded byte entry points."""
    aliases = _symbol_aliases(tree)
    violations: list[str] = []
    if _declared_exports(tree):
        violations.append(f"container exports {_declared_exports(tree)}")
    if _module_level_public_names(tree) != {"annotations"}:
        violations.append(
            f"container public bindings differ: {sorted(_module_level_public_names(tree))}"
        )
    if _module_owned_public_definitions(tree):
        violations.append(
            f"container defines public callables: {sorted(_module_owned_public_definitions(tree))}"
        )

    expected = {
        "_encode_feature_artifact": (
            ["identity", "frame"],
            [],
            {
                "identity": {f"{ROOT_NAME}.artifacts.identity.StageIdentity"},
                "frame": {f"{ROOT_NAME}.core.feature_frames.FeatureFrame"},
            },
            {"bytes"},
        ),
        "_decode_feature_artifact": (
            ["payload"],
            ["expected_identity_key"],
            {"payload": {"bytes"}, "expected_identity_key": {"str"}},
            {"_DecodedFeatureArtifact"},
        ),
    }
    for name, (positional, keyword_only, expected_annotations, returns) in expected.items():
        function = _slice_2e_function(tree, name)
        if function is None:
            violations.append(f"container must define exactly one {name}")
            continue
        observed_annotations = {
            argument.arg: _expression_references(argument.annotation, aliases)
            for argument in [*function.args.args, *function.args.kwonlyargs]
            if argument.annotation is not None
        }
        return_references = (
            _expression_references(function.returns, aliases)
            if function.returns is not None
            else set()
        )
        if (
            not isinstance(function, ast.FunctionDef)
            or function.args.posonlyargs
            or [argument.arg for argument in function.args.args] != positional
            or [argument.arg for argument in function.args.kwonlyargs] != keyword_only
            or function.args.defaults
            or any(default is not None for default in function.args.kw_defaults)
            or function.args.vararg is not None
            or function.args.kwarg is not None
            or observed_annotations != expected_annotations
            or return_references != returns
        ):
            violations.append(f"container {name} signature differs")

    decoded = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "_DecodedFeatureArtifact"
    ]
    if len(decoded) != 1:
        violations.append("container must define one private decoded artifact DTO")
    else:
        decoded_fields = [
            member.target.id
            for member in decoded[0].body
            if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name)
        ]
        if decoded_fields != [
            "_identity_key",
            "_stage_document",
            "_keys",
            "_unit",
            "_clock_domain",
            "_source_identities",
            "_column_names",
            "_columns",
            "_origins",
        ]:
            violations.append(f"decoded artifact DTO fields differ: {decoded_fields}")
        if _slice_2d_dataclass_options(decoded[0], aliases) != [{"frozen": True, "slots": True}]:
            violations.append("decoded artifact DTO must remain frozen and slotted")
    return violations


def _slice_2e_boundary_violations(
    tree: ast.Module,
    *,
    module: str,
) -> list[str]:
    """Enforce exact imports and exclude authority, civil time and extra I/O."""
    expected_imports = (
        SLICE_2E_CONTAINER_IMPORTS if module == "container" else SLICE_2E_STORE_IMPORTS
    )
    forbidden_calls = (
        SLICE_2E_FORBIDDEN_CONTAINER_CALLS
        if module == "container"
        else SLICE_2E_FORBIDDEN_STORE_CALLS
    )
    violations: list[str] = []
    imports = _absolute_import_modules(tree)
    if imports != expected_imports:
        violations.append(
            f"{module} imports differ: expected {sorted(expected_imports)}, "
            f"observed {sorted(imports)}"
        )
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            violations.append(f"{module} uses a relative import")
        if isinstance(node, ast.Import | ast.ImportFrom) and any(
            imported.name == "*" for imported in node.names
        ):
            violations.append(f"{module} uses a wildcard import")

    aliases = _symbol_aliases(tree)
    references = {_reference_leaf(reference) for reference in _expression_references(tree, aliases)}
    violations.extend(
        f"{module} references forbidden {name}"
        for name in sorted(references & SLICE_2E_FORBIDDEN_ARTIFACT_REFERENCES)
    )
    forbidden_library_roots = (
        CIVIL_TIME_MODULES
        | FORBIDDEN_PUBLIC_TYPE_LIBRARIES
        | {"socket", "sqlite3", "tempfile", "urllib"}
    )
    imported_roots = _imported_module_roots(tree) & forbidden_library_roots
    violations.extend(
        f"{module} imports forbidden library {name}" for name in sorted(imported_roots)
    )
    for kind, identifier in _identifier_records(tree):
        if "utc" in identifier.lower():
            violations.append(f"{module} {kind} declares UTC name {identifier!r}")
    direct_calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        direct_calls.update(_slice_2e_direct_call_references(node, aliases))
        called_references = _expression_references(node.func, aliases)
        called = {_reference_leaf(reference) for reference in called_references}
        violations.extend(
            f"{module} calls forbidden capability {name}"
            for name in sorted(called & forbidden_calls)
        )
        for name in sorted(called & SLICE_2E_FORBIDDEN_DYNAMIC_CALLS):
            if name == "compile" and "re.compile" in called_references:
                continue
            violations.append(f"{module} calls dynamic capability {name}")
        if "getattr" in called:
            optional_flag_names = {
                "O_CLOEXEC",
                "O_DIRECTORY",
                "O_NOFOLLOW",
                "O_NONBLOCK",
            }
            if (
                module != "store"
                or len(node.args) != 3
                or node.keywords
                or _expression_references(node.args[0], aliases) != {"os"}
                or not isinstance(node.args[1], ast.Constant)
                or node.args[1].value not in optional_flag_names
                or not isinstance(node.args[2], ast.Constant)
                or node.args[2].value != 0
            ):
                violations.append(f"{module} uses dynamic getattr outside fixed OS flags")
        forbidden_hashes = called & SLICE_2D_FORBIDDEN_HASH_CALLS
        violations.extend(
            f"{module} calls forbidden hash {name}" for name in sorted(forbidden_hashes)
        )
        direct_hash_calls = {
            reference
            for reference in _slice_2e_direct_call_references(node, aliases)
            if reference.startswith("hashlib.")
        }
        unexpected_hashes = direct_hash_calls - {"hashlib.sha256"}
        violations.extend(
            f"{module} calls unapproved hash {name}" for name in sorted(unexpected_hashes)
        )
    if module == "store":
        observed_os_capabilities = {
            reference
            for reference in direct_calls
            if reference.startswith(("fcntl.", "os.", "stat."))
        }
        if observed_os_capabilities != SLICE_2E_STORE_OS_CAPABILITIES:
            violations.append(
                "store OS capabilities differ: "
                f"expected {sorted(SLICE_2E_STORE_OS_CAPABILITIES)}, "
                f"observed {sorted(observed_os_capabilities)}"
            )
    return violations


def _slice_2e_container_format_violations(tree: ast.Module) -> list[str]:
    """Pin canonical NPZ encoding, eager pickle refusal and identity re-hash."""
    aliases = _symbol_aliases(tree)
    violations: list[str] = []
    expected_constants = {
        "_FORMAT": "trading-research-feature-frame",
        "_FORMAT_VERSION": 1,
        "_META_MEMBER": "__meta__",
        "_KEYS_MEMBER": "__keys__",
        "_ZIP_TIMESTAMP": (1980, 1, 1, 0, 0, 0),
    }
    for name, expected in expected_constants.items():
        observed = _slice_2e_literal_assignment(tree, name)
        if observed != expected:
            violations.append(f"container {name} differs: {observed!r}")

    top_level_assignments = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign | ast.AnnAssign)
        and "_TOP_LEVEL_FIELDS" in _assigned_names(node)
        and node.value is not None
    ]
    top_level_fields = (
        {
            child.value
            for child in ast.walk(top_level_assignments[0])
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        }
        if len(top_level_assignments) == 1
        else set()
    )
    if top_level_fields != {
        "columns",
        "format",
        "format_version",
        "identity_key",
        "row_count",
        "stage_identity",
    }:
        violations.append(f"artifact metadata top-level fields differ: {sorted(top_level_fields)}")

    canonical_json = _slice_2e_function(tree, "_canonical_json")
    if canonical_json is None:
        violations.append("container must define one canonical JSON encoder")
    else:
        dumps_calls = [
            node
            for node in ast.walk(canonical_json)
            if isinstance(node, ast.Call)
            and "json.dumps" in _expression_references(node.func, aliases)
        ]
        if len(dumps_calls) != 1:
            violations.append(
                f"canonical JSON must call json.dumps once, observed {len(dumps_calls)}"
            )
        else:
            call = dumps_calls[0]
            keywords = {keyword.arg: keyword.value for keyword in call.keywords}
            separators = keywords.get("separators")
            if (
                len(call.args) != 1
                or not isinstance(call.args[0], ast.Name)
                or call.args[0].id != "value"
                or set(keywords) != {"allow_nan", "ensure_ascii", "separators", "sort_keys"}
                or not isinstance(keywords["sort_keys"], ast.Constant)
                or keywords["sort_keys"].value is not True
                or not isinstance(keywords["ensure_ascii"], ast.Constant)
                or keywords["ensure_ascii"].value is not True
                or not isinstance(keywords["allow_nan"], ast.Constant)
                or keywords["allow_nan"].value is not False
                or not isinstance(separators, ast.Tuple)
                or [
                    item.value if isinstance(item, ast.Constant) else None
                    for item in separators.elts
                ]
                != [",", ":"]
            ):
                violations.append("artifact JSON options are not exact and canonical")

    save_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and "numpy.savez" in _expression_references(node.func, aliases)
    ]
    if len(save_calls) != 1:
        violations.append(f"container must call numpy.savez once, observed {len(save_calls)}")
    else:
        save = save_calls[0]
        if (
            len(save.args) != 1
            or not isinstance(save.args[0], ast.Name)
            or save.args[0].id != "output"
            or len(save.keywords) != 1
            or save.keywords[0].arg is not None
            or not isinstance(save.keywords[0].value, ast.Name)
            or save.keywords[0].value.id != "arrays"
        ):
            violations.append("numpy.savez must receive output and the ordered arrays mapping")
    encode = _slice_2e_function(tree, "_encode_feature_artifact")
    output_buffers = (
        [
            node.value
            for node in ast.walk(encode)
            if isinstance(node, ast.Assign | ast.AnnAssign)
            and "output" in _assigned_names(node)
            and node.value is not None
        ]
        if encode is not None
        else []
    )
    if (
        len(output_buffers) != 1
        or not isinstance(output_buffers[0], ast.Call)
        or "io.BytesIO" not in _expression_references(output_buffers[0].func, aliases)
        or output_buffers[0].args
        or output_buffers[0].keywords
    ):
        violations.append("numpy.savez output must be one fresh in-memory BytesIO")
    if any(
        "numpy.savez_compressed" in _expression_references(node.func, aliases)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    ):
        violations.append("container must never compress its NPZ payload")

    load_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and "numpy.load" in _expression_references(node.func, aliases)
    ]
    if len(load_calls) != 2:
        violations.append(f"container must call numpy.load twice, observed {len(load_calls)}")
    for call in load_calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        source = call.args[0] if len(call.args) == 1 else None
        if (
            set(keywords) != {"allow_pickle"}
            or not isinstance(keywords["allow_pickle"], ast.Constant)
            or keywords["allow_pickle"].value is not False
            or not isinstance(source, ast.Call)
            or "io.BytesIO" not in _expression_references(source.func, aliases)
            or len(source.args) != 1
            or not isinstance(source.args[0], ast.Name)
            or source.args[0].id != "payload"
            or source.keywords
        ):
            violations.append("every numpy.load must read payload with allow_pickle=False")

    load_arrays = _slice_2e_function(tree, "_load_arrays")
    eager_access = False
    if load_arrays is not None:
        for loop in (node for node in ast.walk(load_arrays) if isinstance(node, ast.For)):
            if not isinstance(loop.target, ast.Name) or loop.target.id != "member":
                continue
            eager_access = any(
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "archive"
                and isinstance(node.slice, ast.Name)
                and node.slice.id == "member"
                for statement in loop.body
                for node in ast.walk(statement)
            )
            if eager_access:
                break
    if not eager_access:
        violations.append("decoder must eagerly access every declared array member")

    stage_key = _slice_2e_function(tree, "_stage_key")
    if stage_key is None:
        violations.append("container must define one stage-document key verifier")
    else:
        sha_calls = [
            node
            for node in ast.walk(stage_key)
            if isinstance(node, ast.Call)
            and "hashlib.sha256" in _slice_2e_direct_call_references(node, aliases)
        ]
        if len(sha_calls) != 1:
            violations.append("stage document must feed exactly one SHA-256")
        else:
            sha = sha_calls[0]
            encoded = sha.args[0] if len(sha.args) == 1 and not sha.keywords else None
            canonical_call = (
                encoded.func.value
                if isinstance(encoded, ast.Call)
                and isinstance(encoded.func, ast.Attribute)
                and encoded.func.attr == "encode"
                else None
            )
            if (
                not isinstance(encoded, ast.Call)
                or len(encoded.args) != 1
                or not isinstance(encoded.args[0], ast.Constant)
                or encoded.args[0].value != "utf-8"
                or encoded.keywords
                or not isinstance(canonical_call, ast.Call)
                or _expression_references(canonical_call.func, aliases) != {"_canonical_json"}
                or len(canonical_call.args) != 1
                or not isinstance(canonical_call.args[0], ast.Name)
                or canonical_call.args[0].id != "stage"
                or canonical_call.keywords
            ):
                violations.append("stage SHA-256 must consume canonical JSON UTF-8 bytes")
        hexdigests = [
            node
            for node in ast.walk(stage_key)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "hexdigest"
            and not node.args
            and not node.keywords
        ]
        if len(hexdigests) != 1:
            violations.append("stage SHA-256 must return one lowercase hexdigest")

    decode = _slice_2e_function(tree, "_decode_feature_artifact")
    for label, function, required in (
        (
            "encoder",
            encode,
            {
                "_canonical_document_json",
                "_stage_key",
                "_validate_frame_binding",
                "_validate_zip_envelope",
            },
        ),
        (
            "decoder",
            decode,
            {
                "_load_arrays",
                "_parse_stage_value",
                "_stage_key",
                "_validate_zip_envelope",
            },
        ),
    ):
        called = (
            {
                _reference_leaf(reference)
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
                for reference in _expression_references(node.func, aliases)
            }
            if function is not None
            else set()
        )
        missing = required - called
        if missing:
            violations.append(f"container {label} omits {sorted(missing)}")

    zip_validation = _slice_2e_function(tree, "_validate_zip_envelope")
    zip_calls = (
        [
            node
            for node in ast.walk(zip_validation)
            if isinstance(node, ast.Call)
            and "zipfile.ZipFile" in _expression_references(node.func, aliases)
        ]
        if zip_validation is not None
        else []
    )
    if len(zip_calls) != 1:
        violations.append("ZIP validation must open one in-memory archive")
    else:
        zip_call = zip_calls[0]
        zip_keywords = {keyword.arg: keyword.value for keyword in zip_call.keywords}
        zip_source = zip_call.args[0] if len(zip_call.args) == 1 else None
        if (
            not isinstance(zip_source, ast.Call)
            or "io.BytesIO" not in _expression_references(zip_source.func, aliases)
            or len(zip_source.args) != 1
            or not isinstance(zip_source.args[0], ast.Name)
            or zip_source.args[0].id != "payload"
            or zip_source.keywords
            or set(zip_keywords) != {"mode"}
            or not isinstance(zip_keywords["mode"], ast.Constant)
            or zip_keywords["mode"].value != "r"
        ):
            violations.append("ZIP validation must read payload through BytesIO in mode r")
    zip_references = (
        _expression_references(zip_validation, aliases) if zip_validation is not None else set()
    )
    zip_leaves = {_reference_leaf(reference) for reference in zip_references}
    if not {"ZIP_STORED", "_ZIP_TIMESTAMP"} <= zip_leaves:
        violations.append("ZIP validation must pin uncompressed storage and timestamp")

    column_member = _slice_2e_function(tree, "_column_member")
    returns = (
        [node for node in ast.walk(column_member) if isinstance(node, ast.Return)]
        if column_member is not None
        else []
    )
    if (
        len(returns) != 1
        or not isinstance(returns[0].value, ast.JoinedStr)
        or len(returns[0].value.values) != 2
        or not isinstance(returns[0].value.values[0], ast.Constant)
        or returns[0].value.values[0].value != "__column_"
        or not isinstance(returns[0].value.values[1], ast.FormattedValue)
        or not isinstance(returns[0].value.values[1].value, ast.Name)
        or returns[0].value.values[1].value.id != "index"
    ):
        violations.append("feature arrays must use safe ordinal member names")
    return violations


def _slice_2e_flag_assignment(
    tree: ast.Module,
    name: str,
) -> tuple[set[str], set[str]]:
    """Return direct flag references and ``getattr(os, name)`` flag strings."""
    assignments = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign | ast.AnnAssign)
        and name in _assigned_names(node)
        and node.value is not None
    ]
    if len(assignments) != 1:
        return set(), set()
    aliases = _symbol_aliases(tree)
    expression = assignments[0]
    direct = {
        reference
        for reference in _expression_references(expression, aliases)
        if reference.startswith("os.O_")
    }
    dynamic = {
        node.args[1].value
        for node in ast.walk(expression)
        if isinstance(node, ast.Call)
        and _qualified_name(node.func) == "getattr"
        and len(node.args) == 3
        and not node.keywords
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
        and isinstance(node.args[2], ast.Constant)
        and node.args[2].value == 0
    }
    return direct, dynamic


def _slice_2e_store_durability_violations(tree: ast.Module) -> list[str]:
    """Pin descriptor traversal, identity locking and no-replace publication."""
    aliases = _symbol_aliases(tree)
    violations: list[str] = []
    origins = _module_level_binding_origins(tree)
    required_origins = {
        "_link_at": {"os", "os.link"},
        "_lock_fd": {"fcntl", "fcntl.flock"},
        "_open_fd": {"os", "os.open"},
        "_sync_fd": {"os", "os.fsync"},
    }
    for name, expected in required_origins.items():
        if origins.get(name) != expected:
            violations.append(f"store primitive {name} differs: {sorted(origins.get(name, set()))}")

    expected_flags = {
        "_DIRECTORY_FLAGS": (
            {"os.O_RDONLY"},
            {"O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW"},
        ),
        "_READ_FLAGS": (
            {"os.O_RDONLY"},
            {"O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"},
        ),
        "_TEMP_FLAGS": (
            {"os.O_CREAT", "os.O_EXCL", "os.O_WRONLY"},
            {"O_CLOEXEC", "O_NOFOLLOW"},
        ),
    }
    for name, expected_flag in expected_flags.items():
        if _slice_2e_flag_assignment(tree, name) != expected_flag:
            violations.append(f"store {name} flags differ: {_slice_2e_flag_assignment(tree, name)}")

    expected_constants = {
        "_TEMP_PREFIX": ".feature-artifact-",
        "_TEMP_SUFFIX": ".tmp",
    }
    for name, expected_value in expected_constants.items():
        if _slice_2e_literal_assignment(tree, name) != expected_value:
            violations.append(f"store {name} differs")
    final_patterns = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign | ast.AnnAssign)
        and "_FINAL_NAME" in _assigned_names(node)
        and node.value is not None
    ]
    final_pattern_literals = (
        {
            child.value
            for child in ast.walk(final_patterns[0])
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        }
        if len(final_patterns) == 1
        else set()
    )
    if final_pattern_literals != {r"^([0-9a-f]{64})\.npz$"}:
        violations.append(f"store final-name grammar differs: {final_pattern_literals}")

    open_child = _slice_2e_function(tree, "_open_child_directory")
    child_open_calls = (
        [
            node
            for node in ast.walk(open_child)
            if isinstance(node, ast.Call)
            and "os.open" in _expression_references(node.func, aliases)
        ]
        if open_child is not None
        else []
    )
    if len(child_open_calls) != 1:
        violations.append("child directories must use one descriptor-relative open")
    else:
        call = child_open_calls[0]
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        if (
            len(call.args) != 2
            or not isinstance(call.args[0], ast.Name)
            or call.args[0].id != "name"
            or not isinstance(call.args[1], ast.Name)
            or call.args[1].id != "_DIRECTORY_FLAGS"
            or set(keywords) != {"dir_fd"}
            or not isinstance(keywords["dir_fd"], ast.Name)
            or keywords["dir_fd"].id != "parent_fd"
        ):
            violations.append("child directory open must be relative to its parent FD")

    open_root = _slice_2e_function(tree, "_open_runs_root")
    root_open_calls = (
        [
            node
            for node in ast.walk(open_root)
            if isinstance(node, ast.Call)
            and "os.open" in _expression_references(node.func, aliases)
        ]
        if open_root is not None
        else []
    )
    if len(root_open_calls) != 1:
        violations.append("runs root must begin from one pinned filesystem-root FD")
    else:
        call = root_open_calls[0]
        if (
            len(call.args) != 2
            or not isinstance(call.args[0], ast.Attribute)
            or not isinstance(call.args[0].value, ast.Name)
            or call.args[0].value.id != "root"
            or call.args[0].attr != "anchor"
            or not isinstance(call.args[1], ast.Name)
            or call.args[1].id != "_DIRECTORY_FLAGS"
            or call.keywords
        ):
            violations.append("runs root must open root.anchor with directory no-follow flags")

    read_regular = _slice_2e_function(tree, "_read_regular_file")
    read_open_calls = (
        [
            node
            for node in ast.walk(read_regular)
            if isinstance(node, ast.Call)
            and "os.open" in _expression_references(node.func, aliases)
        ]
        if read_regular is not None
        else []
    )
    read_calls = (
        [node for node in ast.walk(read_regular) if isinstance(node, ast.Call)]
        if read_regular is not None
        else []
    )
    if len(read_open_calls) != 1:
        violations.append("artifact reads must use one descriptor-relative open")
    else:
        call = read_open_calls[0]
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        if (
            len(call.args) != 2
            or not isinstance(call.args[0], ast.Name)
            or call.args[0].id != "name"
            or not isinstance(call.args[1], ast.Name)
            or call.args[1].id != "_READ_FLAGS"
            or set(keywords) != {"dir_fd"}
            or not isinstance(keywords["dir_fd"], ast.Name)
            or keywords["dir_fd"].id != "identity_fd"
        ):
            violations.append("artifact reads must use identity FD and read no-follow flags")
    read_call_leaves = {
        _reference_leaf(reference)
        for call in read_calls
        for reference in _expression_references(call.func, aliases)
    }
    if not {"fstat", "S_ISREG"} <= read_call_leaves:
        violations.append("artifact reads must fstat and refuse non-regular files")

    create_temp = _slice_2e_function(tree, "_create_private_temp")
    temp_open_calls = (
        [
            node
            for node in ast.walk(create_temp)
            if isinstance(node, ast.Call)
            and "os.open" in _expression_references(node.func, aliases)
        ]
        if create_temp is not None
        else []
    )
    if len(temp_open_calls) != 1:
        violations.append("artifact publication must create one private temp FD")
    else:
        call = temp_open_calls[0]
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        if (
            len(call.args) != 3
            or not isinstance(call.args[0], ast.Name)
            or call.args[0].id != "name"
            or not isinstance(call.args[1], ast.Name)
            or call.args[1].id != "_TEMP_FLAGS"
            or not isinstance(call.args[2], ast.Constant)
            or call.args[2].value != 0o600
            or set(keywords) != {"dir_fd"}
            or not isinstance(keywords["dir_fd"], ast.Name)
            or keywords["dir_fd"].id != "identity_fd"
        ):
            violations.append("private temp must use identity FD, exclusive flags and 0600")

    sync_directory = _slice_2e_function(tree, "_sync_directory")
    sync_calls = (
        [
            node
            for node in ast.walk(sync_directory)
            if isinstance(node, ast.Call)
            and "os.fsync" in _expression_references(node.func, aliases)
        ]
        if sync_directory is not None
        else []
    )
    if (
        len(sync_calls) != 1
        or len(sync_calls[0].args) != 1
        or not isinstance(sync_calls[0].args[0], ast.Name)
        or sync_calls[0].args[0].id != "descriptor"
        or sync_calls[0].keywords
    ):
        violations.append("directory durability helper must fsync its exact descriptor")

    create_child = _slice_2e_function(tree, "_create_child_directory")
    create_child_syncs = (
        [
            node
            for node in ast.walk(create_child)
            if isinstance(node, ast.Call)
            and "_sync_directory" in _expression_references(node.func, aliases)
        ]
        if create_child is not None
        else []
    )
    if (
        len(create_child_syncs) != 1
        or not create_child_syncs[0].args
        or not isinstance(create_child_syncs[0].args[0], ast.Name)
        or create_child_syncs[0].args[0].id != "parent_fd"
    ):
        violations.append("new cache directories must fsync their parent FD")

    identity_open = _slice_2e_function(tree, "_open_identity_directory")
    fixed_path = False
    if identity_open is not None:
        fixed_path = any(
            isinstance(node, ast.Tuple)
            and len(node.elts) == 3
            and isinstance(node.elts[0], ast.Constant)
            and node.elts[0].value == "cache"
            and isinstance(node.elts[1], ast.Constant)
            and node.elts[1].value == "features"
            and isinstance(node.elts[2], ast.Name)
            and node.elts[2].id == "key"
            for node in ast.walk(identity_open)
        )
    if not fixed_path:
        violations.append("store path must be exactly cache/features/<identity key>")

    final_names = _slice_2e_function(tree, "_final_names")
    list_calls = (
        [
            node
            for node in ast.walk(final_names)
            if isinstance(node, ast.Call)
            and "os.listdir" in _expression_references(node.func, aliases)
        ]
        if final_names is not None
        else []
    )
    if (
        len(list_calls) != 1
        or len(list_calls[0].args) != 1
        or not isinstance(list_calls[0].args[0], ast.Name)
        or list_calls[0].args[0].id != "identity_fd"
        or list_calls[0].keywords
    ):
        violations.append("final lookup may list only the opened identity directory FD")

    acquire = _slice_2e_function(tree, "_acquire_lock")
    acquire_refs = _expression_references(acquire, aliases) if acquire is not None else set()
    acquire_leaves = {_reference_leaf(reference) for reference in acquire_refs}
    if not {"LOCK_EX", "LOCK_SH", "flock"} <= acquire_leaves:
        violations.append("identity directory lock must support shared and exclusive flock")

    artifact_store = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ArtifactStore"
    ]
    for method_name, lock_value in (("get", False), ("put", True)):
        methods = (
            [
                member
                for member in artifact_store[0].body
                if isinstance(member, ast.FunctionDef) and member.name == method_name
            ]
            if len(artifact_store) == 1
            else []
        )
        lock_calls = (
            [
                node
                for node in ast.walk(methods[0])
                if isinstance(node, ast.Call)
                and "_acquire_lock" in _expression_references(node.func, aliases)
            ]
            if len(methods) == 1
            else []
        )
        if len(lock_calls) != 1:
            violations.append(f"ArtifactStore.{method_name} must acquire one identity lock")
        else:
            keywords = {keyword.arg: keyword.value for keyword in lock_calls[0].keywords}
            exclusive = keywords.get("exclusive")
            if not isinstance(exclusive, ast.Constant) or exclusive.value is not lock_value:
                violations.append(f"ArtifactStore.{method_name} uses the wrong identity lock mode")
        release_calls = (
            [
                node
                for node in ast.walk(methods[0])
                if isinstance(node, ast.Call)
                and "_release_lock" in _expression_references(node.func, aliases)
            ]
            if len(methods) == 1
            else []
        )
        if len(release_calls) != 1:
            violations.append(f"ArtifactStore.{method_name} must release its identity lock")

    release = _slice_2e_function(tree, "_release_lock")
    release_refs = _expression_references(release, aliases) if release is not None else set()
    release_leaves = {_reference_leaf(reference) for reference in release_refs}
    if not {"LOCK_UN", "flock"} <= release_leaves:
        violations.append("identity directory unlock must use flock LOCK_UN")

    publish = _slice_2e_function(tree, "_publish_locked")
    link_calls = (
        [
            node
            for node in ast.walk(publish)
            if isinstance(node, ast.Call)
            and "os.link" in _expression_references(node.func, aliases)
        ]
        if publish is not None
        else []
    )
    if len(link_calls) != 1:
        violations.append("publication must contain one atomic no-replace hard link")
    else:
        link = link_calls[0]
        keywords = {keyword.arg: keyword.value for keyword in link.keywords}
        directory_keyword_values = (
            keywords.get("src_dir_fd"),
            keywords.get("dst_dir_fd"),
        )
        if (
            len(link.args) != 2
            or not isinstance(link.args[0], ast.Name)
            or link.args[0].id != "temporary_name"
            or not isinstance(link.args[1], ast.Name)
            or link.args[1].id != "final_name"
            or set(keywords) != {"dst_dir_fd", "follow_symlinks", "src_dir_fd"}
            or not all(
                isinstance(value, ast.Name) and value.id == "identity_fd"
                for value in directory_keyword_values
            )
            or not isinstance(keywords["follow_symlinks"], ast.Constant)
            or keywords["follow_symlinks"].value is not False
        ):
            violations.append("no-replace link must remain descriptor-relative and no-follow")

    if publish is None:
        violations.append("store must define one locked publisher")
    else:
        called_lines: dict[str, list[int]] = {}
        for node in ast.walk(publish):
            if not isinstance(node, ast.Call):
                continue
            for reference in _expression_references(node.func, aliases):
                called_lines.setdefault(_reference_leaf(reference), []).append(node.lineno)
        required_order = [
            "_write_complete",
            "fsync",
            "_close_file",
            "link",
            "_sync_directory",
        ]
        positions: list[int] = []
        for name in required_order:
            observed = called_lines.get(name, [])
            if not observed:
                violations.append(f"locked publication omits {name}")
                break
            positions.append(min(observed))
        if len(positions) == len(required_order) and positions != sorted(positions):
            violations.append("publication order must be write, file fsync, close, link, dir fsync")
        publish_directory_syncs = [
            node
            for node in ast.walk(publish)
            if isinstance(node, ast.Call)
            and "_sync_directory" in _expression_references(node.func, aliases)
        ]
        if (
            len(publish_directory_syncs) != 1
            or not publish_directory_syncs[0].args
            or not isinstance(publish_directory_syncs[0].args[0], ast.Name)
            or publish_directory_syncs[0].args[0].id != "identity_fd"
        ):
            violations.append("publication must fsync the exact identity directory FD")

    sha_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and "hashlib.sha256" in _slice_2e_direct_call_references(node, aliases)
    ]
    if len(sha_calls) != 4:
        violations.append(
            "store must hash each whole payload at feature and fitted-state put/read "
            f"boundaries, got {len(sha_calls)}"
        )
    for call in sha_calls:
        if (
            len(call.args) != 1
            or not isinstance(call.args[0], ast.Name)
            or call.args[0].id != "payload"
            or call.keywords
        ):
            violations.append("store SHA-256 must consume the entire payload bytes")

    verified = _slice_2e_function(tree, "_verified_final_bytes")
    verified_call_nodes = (
        [node for node in ast.walk(verified) if isinstance(node, ast.Call)]
        if verified is not None
        else []
    )
    verified_calls = {
        _reference_leaf(reference)
        for node in verified_call_nodes
        for reference in _expression_references(node.func, aliases)
    }
    if not {"_decode_feature_artifact", "sha256"} <= verified_calls:
        violations.append("every final read must verify filename hash and container binding")
    verified_sha_calls = [
        node
        for node in verified_call_nodes
        if "hashlib.sha256" in _slice_2e_direct_call_references(node, aliases)
    ]
    verified_decode_calls = [
        node
        for node in verified_call_nodes
        if "_decode_feature_artifact"
        in {_reference_leaf(reference) for reference in _expression_references(node.func, aliases)}
    ]
    filename_hash_guards = (
        [
            node
            for node in ast.walk(verified)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "observed_hash"
            and len(node.test.ops) == 1
            and isinstance(node.test.ops[0], ast.NotEq)
            and len(node.test.comparators) == 1
            and isinstance(node.test.comparators[0], ast.Name)
            and node.test.comparators[0].id == "expected_hash"
            and _body_contains_raise(node.body)
        ]
        if verified is not None
        else []
    )
    if (
        len(verified_sha_calls) != 1
        or len(filename_hash_guards) != 1
        or len(verified_decode_calls) != 1
        or not (
            verified_sha_calls[0].lineno
            < filename_hash_guards[0].lineno
            < verified_decode_calls[0].lineno
        )
    ):
        violations.append("final reads must compare the filename SHA-256 before container decode")

    put_methods = (
        [
            member
            for member in artifact_store[0].body
            if isinstance(member, ast.FunctionDef) and member.name == "put"
        ]
        if len(artifact_store) == 1
        else []
    )
    if len(put_methods) == 1:
        calls = [node for node in ast.walk(put_methods[0]) if isinstance(node, ast.Call)]
        decode_lines = [
            node.lineno
            for node in calls
            if "_decode_feature_artifact"
            in {
                _reference_leaf(reference)
                for reference in _expression_references(node.func, aliases)
            }
        ]
        open_lines = [
            node.lineno
            for node in calls
            if "_open_identity_directory"
            in {
                _reference_leaf(reference)
                for reference in _expression_references(node.func, aliases)
            }
        ]
        if len(decode_lines) != 1 or len(open_lines) != 1 or decode_lines[0] >= open_lines[0]:
            violations.append("put must validate all bytes before touching the filesystem")
    return violations


def _slice_2e_conflict_violations(tree: ast.Module) -> list[str]:
    """Require every multi-content observation to stay a loud hard failure."""
    aliases = _symbol_aliases(tree)
    stores = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ArtifactStore"
    ]
    functions: dict[str, ast.AST | None] = {
        "get": None,
        "put": None,
        "_publish_locked": _slice_2e_function(tree, "_publish_locked"),
    }
    if len(stores) == 1:
        for name in ("get", "put"):
            matches = [
                member
                for member in stores[0].body
                if isinstance(member, ast.FunctionDef) and member.name == name
            ]
            functions[name] = matches[0] if len(matches) == 1 else None

    minimum_conflicts = {"get": 1, "put": 2, "_publish_locked": 1}
    violations: list[str] = []
    for name, function in functions.items():
        if function is None:
            violations.append(f"store conflict path {name} is absent")
            continue
        calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
        conflict_calls = [
            node
            for node in calls
            if "_ArtifactConflictError" in _expression_references(node.func, aliases)
        ]
        if len(conflict_calls) < minimum_conflicts[name]:
            violations.append(f"store {name} has only {len(conflict_calls)} hard conflict refusals")
        called_leaves = {
            _reference_leaf(reference)
            for node in calls
            for reference in _expression_references(node.func, aliases)
        }
        if name in {"get", "put"} and "_final_names" not in called_leaves:
            violations.append(f"store {name} does not inspect the complete final inventory")
        if name == "_publish_locked" and "_verified_final_bytes" not in called_leaves:
            violations.append("concurrent target collision is not fully verified")

    final_names = _slice_2e_function(tree, "_final_names")
    inert_temp_shape = False
    malformed_final_refusal = False
    if final_names is not None:
        for condition in (node for node in ast.walk(final_names) if isinstance(node, ast.If)):
            references = _expression_references(condition.test, aliases)
            leaves = {_reference_leaf(reference) for reference in references}
            if "endswith" in leaves and any(
                isinstance(node, ast.Continue) for node in condition.body
            ):
                inert_temp_shape = True
            if "fullmatch" in leaves and _body_contains_raise(condition.body):
                malformed_final_refusal = True
    if not inert_temp_shape:
        violations.append("non-NPZ temporary names must remain inert during lookup")
    if not malformed_final_refusal:
        violations.append("NPZ-looking malformed final names must be refused")
    return violations


def _slice_2e_test_import_violations() -> list[str]:
    """Keep artifact qualification synthetic and outside data authority."""
    violations: list[str] = []
    for name in SLICE_2E_TEST_MODULES:
        path = Path(__file__).with_name(name)
        if not path.is_file():
            violations.append(f"missing Slice 2E qualification module {name}")
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for module in sorted(_absolute_import_modules(tree)):
            if not any(
                module == prefix or module.startswith(f"{prefix}.")
                for prefix in SLICE_2E_ALLOWED_TEST_IMPORT_PREFIXES
            ):
                violations.append(f"{name} imports outside synthetic budget: {module}")
            if module.startswith((f"{ROOT_NAME}.data", f"{ROOT_NAME}.experiments")):
                violations.append(f"{name} reaches forbidden authority layer {module}")
    return violations


def _slice_2e_mutated_tree(source: str, old: str, new: str) -> ast.Module:
    """Apply one bounded mutation and refuse a vacuous mutation proof."""
    assert source.count(old) == 1, f"mutation anchor is not unique: {old!r}"
    return ast.parse(source.replace(old, new, 1))


def _slice_2c_leakage_tree() -> ast.Module:
    """Parse the mandatory release-blocking Slice 2C leakage test module."""
    assert SLICE_2C_LEAKAGE_TEST_PATH.is_file(), "Slice 2C requires tests/test_feature_leakage.py"
    return ast.parse(
        SLICE_2C_LEAKAGE_TEST_PATH.read_text(),
        filename=str(SLICE_2C_LEAKAGE_TEST_PATH),
    )


def _slice_2c_control_inventory_violations(tree: ast.Module) -> list[str]:
    """Return deviations from the six finite test-only adversarial providers."""
    public_provider_classes = [
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name.endswith("Provider")
        and not node.name.startswith("_")
    ]
    counts = {name: public_provider_classes.count(name) for name in public_provider_classes}
    observed = set(public_provider_classes)
    violations: list[str] = []
    if observed != SLICE_2C_NEGATIVE_CONTROL_CLASSES:
        violations.append(
            "negative-control classes differ: "
            f"expected {sorted(SLICE_2C_NEGATIVE_CONTROL_CLASSES)}, "
            f"observed {sorted(observed)}"
        )
    duplicates = sorted(name for name, count in counts.items() if count != 1)
    if duplicates:
        violations.append(f"negative-control classes are not unique: {duplicates}")
    return violations


def _slice_2c_references_live_registry(
    node: ast.AST,
    aliases: dict[str, set[str]],
) -> bool:
    """Whether an expression resolves to the production feature registry."""
    return any(
        reference.endswith(".FEATURE_PROVIDERS") or ".FEATURE_PROVIDERS." in reference
        for reference in _expression_references(node, aliases)
    )


def _slice_2c_registry_mutation_violations(tree: ast.Module) -> list[str]:
    """Reject every bounded mutation route to the live feature registry."""
    aliases = _symbol_aliases(tree)
    direct_registry_bindings = {
        imported.asname or imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for imported in node.names
        if imported.name == "FEATURE_PROVIDERS"
    }
    forbidden_methods = {
        "__delitem__",
        "__setitem__",
        "add",
        "clear",
        "pop",
        "popitem",
        "register",
        "setdefault",
        "update",
    }
    violations: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "_factories"
            and _slice_2c_references_live_registry(node, aliases)
        ):
            violations.add("accesses FEATURE_PROVIDERS._factories")

        if isinstance(node, ast.Call):
            called = _expression_references(node.func, aliases)
            mutations = sorted(
                reference
                for reference in called
                if _slice_2c_references_live_registry(node.func, aliases)
                and _reference_leaf(reference) in forbidden_methods
            )
            if mutations:
                violations.add(f"calls registry mutation {mutations}")
            if (
                any(_reference_leaf(reference) in {"delattr", "setattr"} for reference in called)
                and node.args
                and _slice_2c_references_live_registry(node.args[0], aliases)
            ):
                violations.add("mutates FEATURE_PROVIDERS through setattr/delattr")

        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets.extend(node.targets)
        elif isinstance(node, ast.AnnAssign | ast.AugAssign):
            targets.append(node.target)
        elif isinstance(node, ast.Delete):
            targets.extend(node.targets)
        if any(
            (isinstance(target, ast.Name) and target.id in direct_registry_bindings)
            or (
                not isinstance(target, ast.Name)
                and _slice_2c_references_live_registry(target, aliases)
            )
            for target in targets
        ):
            violations.add("assigns to or deletes FEATURE_PROVIDERS")
    return sorted(violations)


def _slice_2c_import_is_allowed(module: str) -> bool:
    """Return whether a leakage-test import is synthetic and authority-free."""
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in SLICE_2C_ALLOWED_LEAKAGE_IMPORT_PREFIXES
    )


def _slice_2c_imported_symbol_leaves(tree: ast.Module) -> set[str]:
    """Return imported module/symbol leaves even when locally aliased or unused."""
    leaves: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            leaves.update(imported.name.rsplit(".", maxsplit=1)[-1] for imported in node.names)
    return leaves


def _slice_2c_authority_violations(tree: ast.Module) -> list[str]:
    """Reject data access, nondeterminism and I/O from the synthetic rigs."""
    aliases = _symbol_aliases(tree)
    violations: set[str] = {
        f"imports {module}"
        for module in _absolute_import_modules(tree)
        if not _slice_2c_import_is_allowed(module)
    }
    violations.update(
        f"imports authority binding {name}"
        for name in sorted(
            _slice_2c_imported_symbol_leaves(tree) & SLICE_2C_FORBIDDEN_AUTHORITY_IDENTIFIERS
        )
    )
    references = _expression_references(tree, aliases)
    forbidden_identifiers = sorted(
        reference
        for reference in references
        if _reference_leaf(reference) in SLICE_2C_FORBIDDEN_AUTHORITY_IDENTIFIERS
    )
    violations.update(f"references authority {name}" for name in forbidden_identifiers)
    random_references = sorted(
        reference
        for reference in references
        if reference == "numpy.random" or reference.startswith("numpy.random.")
    )
    violations.update(f"references nondeterminism {name}" for name in random_references)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = {
            _reference_leaf(reference) for reference in _expression_references(node.func, aliases)
        }
        violations.update(
            f"calls authority {name}"
            for name in sorted(called & SLICE_2C_FORBIDDEN_AUTHORITY_CALLS)
        )
    return sorted(violations)


def _slice_2c_gate_evasion_violations(tree: ast.Module) -> list[str]:
    """Reject skips, expected failures, excluded markers and tolerant equality."""
    aliases = _symbol_aliases(tree)
    violations = {
        f"imports forbidden gate binding {name}"
        for name in sorted(
            _slice_2c_imported_symbol_leaves(tree) & SLICE_2C_FORBIDDEN_GATE_EVASION_REFERENCES
        )
    }
    references = _expression_references(tree, aliases)
    forbidden_references = sorted(
        reference
        for reference in references
        if _reference_leaf(reference) in SLICE_2C_FORBIDDEN_GATE_EVASION_REFERENCES
    )
    violations.update(f"references {reference}" for reference in forbidden_references)
    performance_references = sorted(
        reference
        for reference in references
        if reference.endswith(".mark.performance") or reference == "performance"
    )
    violations.update(
        f"uses default-excluded performance marker {reference}"
        for reference in performance_references
    )

    for statement in tree.body:
        if isinstance(
            statement, ast.Assign | ast.AnnAssign | ast.TypeAlias
        ) and "pytestmark" in _assigned_names(statement):
            violations.add("assigns module-level pytestmark")

    for node in ast.walk(tree):
        identifier: str | None = None
        if isinstance(node, ast.Name):
            identifier = node.id
        elif isinstance(node, ast.arg):
            identifier = node.arg
        if identifier in SLICE_2C_FORBIDDEN_TOLERANCE_IDENTIFIERS:
            violations.add(f"declares tolerance identifier {identifier!r}")

        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg in SLICE_2C_FORBIDDEN_TOLERANCE_IDENTIFIERS | {
                    "decimal",
                    "delta",
                    "places",
                }:
                    violations.add(f"passes tolerance keyword {keyword.arg!r}")

        if isinstance(node, ast.Compare):
            contains_distance = any(
                isinstance(child, ast.BinOp) and isinstance(child.op, ast.Sub)
                for child in ast.walk(node)
            ) or any(
                isinstance(child, ast.Call)
                and any(
                    _reference_leaf(reference) == "abs"
                    for reference in _expression_references(child.func, aliases)
                )
                for child in ast.walk(node)
            )
            contains_nonzero_float = any(
                isinstance(child, ast.Constant)
                and isinstance(child.value, float)
                and child.value != 0.0
                for child in ast.walk(node)
            )
            if contains_distance and contains_nonzero_float:
                violations.add("uses a manual floating-distance tolerance")
    return sorted(violations)


def _slice_2c_uint64_tobytes_root(
    node: ast.AST,
    aliases: dict[str, set[str]],
) -> str | None:
    """Return the array name in ``array.view(np.uint64).tobytes()``."""
    if (
        not isinstance(node, ast.Call)
        or node.args
        or node.keywords
        or not isinstance(node.func, ast.Attribute)
        or node.func.attr != "tobytes"
    ):
        return None
    view = node.func.value
    if (
        not isinstance(view, ast.Call)
        or len(view.args) != 1
        or view.keywords
        or not isinstance(view.func, ast.Attribute)
        or view.func.attr != "view"
        or not isinstance(view.func.value, ast.Name)
    ):
        return None
    dtype_references = {
        reference
        for reference in _expression_references(view.args[0], aliases)
        if reference == "numpy.uint64"
    }
    if dtype_references != {"numpy.uint64"}:
        return None
    return view.func.value.id


def _slice_2c_exact_column_argument_root(node: ast.AST) -> str | None:
    """Return ``left``/``right`` from ``frame.column(name)[positions]``."""
    if not isinstance(node, ast.Subscript) or not isinstance(node.value, ast.Call):
        return None
    column_call = node.value
    if (
        len(column_call.args) != 1
        or column_call.keywords
        or not isinstance(column_call.func, ast.Attribute)
        or column_call.func.attr != "column"
        or not isinstance(column_call.func.value, ast.Name)
    ):
        return None
    return column_call.func.value.id


def _slice_2c_exact_bit_comparison_violations(tree: ast.Module) -> list[str]:
    """Pin the value comparator itself and its use by frame comparisons."""
    aliases = _symbol_aliases(tree)
    violations: list[str] = []
    helpers = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_assert_array_bits_equal"
    ]
    if len(helpers) != 1:
        return [f"expected exactly one top-level _assert_array_bits_equal, found {len(helpers)}"]
    helper = helpers[0]
    if (
        helper.args.posonlyargs
        or [argument.arg for argument in helper.args.args] != ["left", "right"]
        or helper.args.defaults
        or helper.args.kwonlyargs
        or helper.args.vararg is not None
        or helper.args.kwarg is not None
    ):
        violations.append("_assert_array_bits_equal must accept exactly left and right")

    raw_bit_pairs: list[tuple[str, str]] = []
    for statement in helper.body:
        if not isinstance(statement, ast.Assert | ast.If):
            continue
        comparison = statement.test
        if (
            not isinstance(comparison, ast.Compare)
            or len(comparison.ops) != 1
            or len(comparison.comparators) != 1
        ):
            continue
        comparison_enforces_equality = (
            isinstance(statement, ast.Assert) and isinstance(comparison.ops[0], ast.Eq)
        ) or (
            isinstance(statement, ast.If)
            and isinstance(comparison.ops[0], ast.NotEq)
            and any(isinstance(child, ast.Raise) for child in statement.body)
        )
        if not comparison_enforces_equality:
            continue
        left_root = _slice_2c_uint64_tobytes_root(comparison.left, aliases)
        right_root = _slice_2c_uint64_tobytes_root(
            comparison.comparators[0],
            aliases,
        )
        if left_root is not None and right_root is not None:
            raw_bit_pairs.append((left_root, right_root))
    if raw_bit_pairs != [("left", "right")]:
        violations.append(
            "_assert_array_bits_equal must directly compare "
            "left.view(np.uint64).tobytes() with "
            f"right.view(np.uint64).tobytes(); observed {raw_bit_pairs}"
        )

    frame_helpers = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_assert_frame_values_exact"
    ]
    if len(frame_helpers) != 1:
        violations.append(
            f"expected exactly one top-level _assert_frame_values_exact, found {len(frame_helpers)}"
        )
    else:
        exact_call_pairs: list[tuple[str, str]] = []
        for node in ast.walk(frame_helpers[0]):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Name)
                or node.func.id != "_assert_array_bits_equal"
                or len(node.args) != 2
                or node.keywords
            ):
                continue
            left_root = _slice_2c_exact_column_argument_root(node.args[0])
            right_root = _slice_2c_exact_column_argument_root(node.args[1])
            if left_root is not None and right_root is not None:
                exact_call_pairs.append((left_root, right_root))
        if exact_call_pairs != [("left", "right")]:
            violations.append(
                "_assert_frame_values_exact must pass its left/right feature columns "
                f"to _assert_array_bits_equal exactly once; observed {exact_call_pairs}"
            )
    return violations


def _slice_2f_function_signature_violations(
    tree: ast.Module,
    *,
    name: str,
    positional: list[str],
    keyword_only: list[str],
    annotations: dict[str, set[str]],
    returns: set[str],
    keyword_defaults: dict[str, set[str] | None] | None = None,
) -> list[str]:
    """Pin one non-variadic typed function without evaluating its module."""
    function = _slice_2e_function(tree, name)
    if function is None or not isinstance(function, ast.FunctionDef):
        return [f"expected one synchronous {name}"]
    aliases = _symbol_aliases(tree)
    observed_annotations = {
        argument.arg: _expression_references(argument.annotation, aliases)
        for argument in [*function.args.args, *function.args.kwonlyargs]
        if argument.annotation is not None
    }
    observed_returns = (
        _expression_references(function.returns, aliases) if function.returns is not None else set()
    )
    expected_defaults = keyword_defaults or dict.fromkeys(keyword_only)
    observed_defaults = {
        argument.arg: (None if default is None else _expression_references(default, aliases))
        for argument, default in zip(
            function.args.kwonlyargs,
            function.args.kw_defaults,
            strict=True,
        )
    }
    if (
        function.args.posonlyargs
        or [argument.arg for argument in function.args.args] != positional
        or [argument.arg for argument in function.args.kwonlyargs] != keyword_only
        or function.args.defaults
        or function.args.vararg is not None
        or function.args.kwarg is not None
        or observed_annotations != annotations
        or observed_returns != returns
        or observed_defaults != expected_defaults
    ):
        return [
            f"{name} signature differs: positional="
            f"{[argument.arg for argument in function.args.args]}, keyword_only="
            f"{[argument.arg for argument in function.args.kwonlyargs]}, "
            f"annotations={observed_annotations}, returns={observed_returns}, "
            f"defaults={observed_defaults}"
        ]
    return []


def _slice_2f_policy_surface_violations(tree: ast.Module) -> list[str]:
    """Keep policy vocabulary closed and its module otherwise private."""
    violations: list[str] = []
    if _declared_exports(tree) != {"MaterializationPolicy"}:
        violations.append(f"materialization exports differ: {sorted(_declared_exports(tree))}")
    if _module_level_public_names(tree) != {
        "MaterializationPolicy",
        "annotations",
    }:
        violations.append(
            f"materialization public bindings differ: {sorted(_module_level_public_names(tree))}"
        )
    policies = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MaterializationPolicy"
    ]
    if len(policies) != 1:
        return [*violations, f"expected one MaterializationPolicy, found {len(policies)}"]
    policy = policies[0]
    aliases = _symbol_aliases(tree)
    bases = set().union(
        *(_expression_references(base, aliases) for base in policy.bases),
        set(),
    )
    if bases != {"enum.StrEnum"} or policy.keywords:
        violations.append(f"MaterializationPolicy must directly subclass StrEnum: {bases}")
    observed: dict[str, object] = {}
    for member in policy.body:
        if not isinstance(member, ast.Assign) or len(member.targets) != 1:
            continue
        target = member.targets[0]
        if not isinstance(target, ast.Name) or target.id.startswith("_"):
            continue
        try:
            observed[target.id] = ast.literal_eval(member.value)
        except (TypeError, ValueError):
            observed[target.id] = None
    if observed != SLICE_2F_POLICY_MEMBERS:
        violations.append(f"MaterializationPolicy members differ: {observed}")
    if _class_public_surface(policy):
        violations.append(
            "MaterializationPolicy adds public methods or annotated fields: "
            f"{sorted(_class_public_surface(policy))}"
        )
    return violations


def _slice_2f_provider_snapshot_violations(tree: ast.Module) -> list[str]:
    """Pin frozen declaration snapshots and exact post-lookup comparison."""
    violations: list[str] = []
    aliases = _symbol_aliases(tree)
    plans = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "_ProviderPlan"
    ]
    if len(plans) != 1:
        return [f"expected one _ProviderPlan, found {len(plans)}"]
    plan = plans[0]
    fields = [
        member.target.id
        for member in plan.body
        if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name)
    ]
    if fields != [
        "_index",
        "_origin",
        "_feature_names",
        "_required_columns",
        "_cost_hint",
        "_compute",
    ]:
        violations.append(f"_ProviderPlan fields differ: {fields}")
    if _slice_2d_dataclass_options(plan, aliases) != [{"frozen": True, "slots": True}]:
        violations.append("_ProviderPlan must remain frozen and slotted")
    if _class_public_surface(plan) != SLICE_2B_PROVIDER_SURFACE:
        violations.append(
            f"_ProviderPlan provider facade differs: {sorted(_class_public_surface(plan))}"
        )
    expected_properties = SLICE_2B_PROVIDER_SURFACE - {"compute"}
    observed_properties = {
        member.name
        for member in plan.body
        if isinstance(member, ast.FunctionDef)
        and any(
            _expression_references(decorator, aliases) == {"property"}
            for decorator in member.decorator_list
        )
    }
    if observed_properties != expected_properties:
        violations.append(f"_ProviderPlan properties differ: {sorted(observed_properties)}")
    expected_returns = {
        "provider_id": "self._origin.provider_id",
        "version": "self._origin.version",
        "params": "self._origin.params",
        "window": "self._origin.window",
        "feature_names": "self._feature_names",
        "required_columns": "self._required_columns",
        "cost_hint": "self._cost_hint",
    }
    for member in plan.body:
        if not isinstance(member, ast.FunctionDef) or member.name not in expected_returns:
            continue
        returns = [node for node in ast.walk(member) if isinstance(node, ast.Return)]
        observed = (
            _qualified_name(returns[0].value)
            if len(returns) == 1 and returns[0].value is not None
            else None
        )
        if observed != expected_returns[member.name]:
            violations.append(
                f"_ProviderPlan.{member.name} returns {observed!r}, expected "
                f"{expected_returns[member.name]!r}"
            )
    compute_methods = [
        member
        for member in plan.body
        if isinstance(member, ast.FunctionDef) and member.name == "compute"
    ]
    if len(compute_methods) == 1:
        captured_calls = [
            node
            for node in ast.walk(compute_methods[0])
            if isinstance(node, ast.Call)
            and _qualified_name(node.func) == "self._compute"
            and [_qualified_name(argument) for argument in node.args] == ["request"]
            and not node.keywords
        ]
        if len(captured_calls) != 1:
            violations.append("_ProviderPlan.compute must invoke only captured _compute")

    violations.extend(
        _slice_2f_function_signature_violations(
            tree,
            name="_validated_plan",
            positional=["providers"],
            keyword_only=[],
            annotations={"providers": {"object"}},
            returns={"_ProviderPlan", "tuple"},
        )
    )
    for name, positional, keyword_only, signature_annotations, signature_returns in (
        (
            "_validated_provider_objects",
            ["providers"],
            [],
            {"providers": {"object"}},
            {"object", "tuple"},
        ),
        (
            "_validated_plan_from_objects",
            ["provider_objects"],
            [],
            {"provider_objects": {"object", "tuple"}},
            {"_ProviderPlan", "tuple"},
        ),
        (
            "_revalidated_provider_plan",
            [],
            ["provider_objects", "expected"],
            {
                "provider_objects": {"object", "tuple"},
                "expected": {"_ProviderPlan", "tuple"},
            },
            {"_ProviderPlan", "tuple"},
        ),
        (
            "_same_provider_declaration",
            ["left", "right"],
            [],
            {"left": {"_ProviderPlan"}, "right": {"_ProviderPlan"}},
            {"bool"},
        ),
    ):
        violations.extend(
            _slice_2f_function_signature_violations(
                tree,
                name=name,
                positional=positional,
                keyword_only=keyword_only,
                annotations=signature_annotations,
                returns=signature_returns,
            )
        )
    violations.extend(
        _slice_2f_function_signature_violations(
            tree,
            name="_execute_feature_plans",
            positional=[],
            keyword_only=[
                "frames",
                "eval_start",
                "eval_end",
                "expected_eval_keys",
                "plans",
                "short_context_accepted",
                "capture_provider_failure",
            ],
            annotations={
                "frames": {
                    "collections.abc.Sequence",
                    f"{ROOT_NAME}.core.frames.MarketFrame",
                },
                "eval_start": {f"{ROOT_NAME}.core.time.NativeTimeKey"},
                "eval_end": {f"{ROOT_NAME}.core.time.NativeTimeKey"},
                "expected_eval_keys": {
                    "numpy",
                    "numpy.int64",
                    "numpy.typing",
                    "numpy.typing.NDArray",
                },
                "plans": {"_ProviderPlan", "tuple"},
                "short_context_accepted": {"bool"},
                "capture_provider_failure": {"bool"},
            },
            returns={f"{ROOT_NAME}.core.feature_frames.FeatureFrame"},
        )
    )

    snapshot = _slice_2e_function(tree, "_snapshot_provider")
    if snapshot is None:
        violations.append("execution must define one _snapshot_provider")
    else:
        constructors = [
            node
            for node in ast.walk(snapshot)
            if isinstance(node, ast.Call) and _qualified_name(node.func) == "_ProviderPlan"
        ]
        if len(constructors) != 1:
            violations.append(f"_snapshot_provider must mint one plan, found {len(constructors)}")
        else:
            constructor = constructors[0]
            keyword_values = {
                keyword.arg: _qualified_name(keyword.value) for keyword in constructor.keywords
            }
            expected_values = {
                "_index": "index",
                "_origin": "origin",
                "_feature_names": "feature_names",
                "_required_columns": "required_columns",
                "_cost_hint": "cost_hint",
                "_compute": "compute",
            }
            if constructor.args or keyword_values != expected_values:
                violations.append(f"_snapshot_provider plan binding differs: {keyword_values}")

    prepared = _slice_2e_function(tree, "_execute_feature_plans")
    if prepared is not None:
        prepared_names = {node.id for node in ast.walk(prepared) if isinstance(node, ast.Name)}
        if "providers" in prepared_names:
            violations.append("prepared execution rereads raw providers")
        forbidden_calls = {
            leaf
            for leaf, _line, _call in _function_call_records(prepared, aliases)
            if leaf in {"_validated_plan", "_snapshot_provider", "getattr"}
        }
        violations.extend(
            f"prepared execution calls forbidden {name}" for name in sorted(forbidden_calls)
        )

    wrapper = _slice_2e_function(tree, "_execute_features")
    if wrapper is None:
        violations.append("execution must retain one _execute_features adapter")
    else:
        records = _function_call_records(wrapper, aliases)
        if [leaf for leaf, _line, _call in records].count("_validated_plan") != 1:
            violations.append("_execute_features must snapshot the provider plan once")
        plan_calls = [call for leaf, _line, call in records if leaf == "_execute_feature_plans"]
        if len(plan_calls) != 1:
            violations.append("_execute_features must call prepared execution once")
        else:
            keywords = {keyword.arg: keyword.value for keyword in plan_calls[0].keywords}
            plan_value = keywords.get("plans")
            capture = keywords.get("capture_provider_failure")
            if not isinstance(plan_value, ast.Name) or plan_value.id != "plans":
                violations.append("_execute_features must execute its exact plans snapshot")
            if not isinstance(capture, ast.Constant) or capture.value is not False:
                violations.append("compute_features must preserve original exceptions")

    object_validator = _slice_2e_function(tree, "_validated_provider_objects")
    if object_validator is not None:
        tuple_calls = [
            node
            for node in ast.walk(object_validator)
            if isinstance(node, ast.Call)
            and _qualified_name(node.func) == "tuple"
            and [_qualified_name(argument) for argument in node.args] == ["providers"]
            and not node.keywords
        ]
        if len(tuple_calls) != 1:
            violations.append("provider Sequence must be detached by one tuple(providers)")
        providers_loads = [
            node
            for node in ast.walk(object_validator)
            if isinstance(node, ast.Name)
            and node.id == "providers"
            and isinstance(node.ctx, ast.Load)
        ]
        expected_load_parents = {"isinstance", "tuple"}
        observed_load_parents: list[str | None] = []
        parent_by_child = {
            child: parent
            for parent in ast.walk(object_validator)
            for child in ast.iter_child_nodes(parent)
        }
        for provider_load in providers_loads:
            parent = parent_by_child.get(provider_load)
            observed_load_parents.append(
                _qualified_name(parent.func) if isinstance(parent, ast.Call) else None
            )
        if len(providers_loads) != 3 or set(observed_load_parents) != expected_load_parents:
            violations.append(
                "provider Sequence may be read only by its two type checks and one tuple detach"
            )
        returns = [node for node in ast.walk(object_validator) if isinstance(node, ast.Return)]
        if (
            len(returns) != 1
            or not isinstance(returns[0].value, ast.Name)
            or returns[0].value.id != "provider_objects"
        ):
            violations.append("provider Sequence validator must return only provider_objects")

    revalidator = _slice_2e_function(tree, "_revalidated_provider_plan")
    if revalidator is not None:
        records = _function_call_records(revalidator, aliases)
        if [leaf for leaf, _line, _call in records].count("_validated_plan_from_objects") != 1:
            violations.append("post-lookup revalidation must resnapshot provider objects once")
        if [leaf for leaf, _line, _call in records].count("_same_provider_declaration") != 1:
            violations.append("post-lookup revalidation must compare every declaration")
        resnapshot_calls = [
            call for leaf, _line, call in records if leaf == "_validated_plan_from_objects"
        ]
        if len(resnapshot_calls) == 1 and (
            [_qualified_name(argument) for argument in resnapshot_calls[0].args]
            != ["provider_objects"]
            or resnapshot_calls[0].keywords
        ):
            violations.append("post-lookup resnapshot must consume provider_objects")
        observed_assignments = [
            statement
            for statement in revalidator.body
            if isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and statement.targets[0].id == "observed"
        ]
        if (
            len(observed_assignments) != 1
            or not isinstance(observed_assignments[0].value, ast.Call)
            or _qualified_name(observed_assignments[0].value.func) != "_validated_plan_from_objects"
        ):
            violations.append("post-lookup resnapshot must bind once as observed")
        comparison_loops = [node for node in revalidator.body if isinstance(node, ast.For)]
        expected_iterator = ast.parse(
            "zip(expected, observed, strict=True)",
            mode="eval",
        ).body
        target_names: list[str] = []
        if len(comparison_loops) == 1 and isinstance(
            comparison_loops[0].target,
            ast.Tuple,
        ):
            for element in comparison_loops[0].target.elts:
                if isinstance(element, ast.Name):
                    target_names.append(element.id)
        if (
            len(comparison_loops) != 1
            or target_names != ["before", "after"]
            or ast.dump(comparison_loops[0].iter) != ast.dump(expected_iterator)
        ):
            violations.append(
                "post-lookup revalidation must pair expected and observed plans strictly"
            )
        elif len(comparison_loops[0].body) != 1:
            violations.append("each provider declaration comparison must have one refusal branch")
        else:
            refusal = comparison_loops[0].body[0]
            expected_refusal = ast.parse(
                "not _same_provider_declaration(before, after)",
                mode="eval",
            ).body
            if (
                not isinstance(refusal, ast.If)
                or ast.dump(refusal.test) != ast.dump(expected_refusal)
                or not any(isinstance(node, ast.Raise) for node in refusal.body)
                or refusal.orelse
            ):
                violations.append(
                    "post-lookup revalidation must refuse each unequal declaration pair"
                )
        returns = [node for node in ast.walk(revalidator) if isinstance(node, ast.Return)]
        if (
            len(returns) != 1
            or not isinstance(returns[0].value, ast.Name)
            or returns[0].value.id != "observed"
        ):
            violations.append("post-lookup execution must use the refreshed equivalent plan")

    comparator = _slice_2e_function(tree, "_same_provider_declaration")
    if comparator is not None:
        returns = [node for node in ast.walk(comparator) if isinstance(node, ast.Return)]
        expected_comparison = ast.parse(
            "left._index == right._index "
            "and left.provider_id == right.provider_id "
            "and left.version == right.version "
            "and _canonical_params_json(left.params) "
            "== _canonical_params_json(right.params) "
            "and left.window == right.window "
            "and left.feature_names == right.feature_names "
            "and left.required_columns == right.required_columns "
            "and left.cost_hint is right.cost_hint",
            mode="eval",
        ).body
        if (
            len(returns) != 1
            or returns[0].value is None
            or ast.dump(returns[0].value) != ast.dump(expected_comparison)
        ):
            violations.append(
                "provider revalidation must compare every identity/cost declaration exactly"
            )
    return violations


def _slice_2f_restore_factory_violations(tree: ast.Module) -> list[str]:
    """Keep cached-frame restoration private, fully typed and sole-minting."""
    violations = _slice_2f_function_signature_violations(
        tree,
        name="_restore_feature_frame",
        positional=[],
        keyword_only=[
            "keys",
            "unit",
            "clock_domain",
            "source_identities",
            "columns",
            "origins",
        ],
        annotations={
            "keys": {
                "numpy.typing.NDArray",
                "numpy.typing",
                "typing.Any",
                "typing",
            },
            "unit": {f"{ROOT_NAME}.core.time.TimeUnit"},
            "clock_domain": {f"{ROOT_NAME}.core.time.ClockDomain"},
            "source_identities": {
                f"{ROOT_NAME}.core.identity.DatasetIdentity",
                "tuple",
            },
            "columns": {
                "collections.abc.Mapping",
                "numpy.typing",
                "numpy.typing.NDArray",
                "str",
                "typing.Any",
                "typing",
            },
            "origins": {
                "collections.abc.Mapping",
                f"{ROOT_NAME}.core.features.FeatureOrigin",
                "str",
            },
        },
        returns={"FeatureFrame"},
    )
    restore = _slice_2e_function(tree, "_restore_feature_frame")
    if restore is None:
        return violations
    mint_calls = [
        (owner.name, node)
        for owner in tree.body
        if isinstance(owner, ast.FunctionDef | ast.AsyncFunctionDef)
        for node in ast.walk(owner)
        if isinstance(node, ast.Call)
        and _qualified_name(node.func) == "object.__new__"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "FeatureFrame"
    ]
    if [name for name, _call in mint_calls] != ["_restore_feature_frame"]:
        violations.append(
            "FeatureFrame minting must occur only in _restore_feature_frame: "
            f"{[name for name, _call in mint_calls]}"
        )
    make = _slice_2e_function(tree, "_make_feature_frame")
    if make is None:
        violations.append("core must retain one _make_feature_frame")
    else:
        calls = [
            call
            for leaf, _line, call in _function_call_records(make, _symbol_aliases(tree))
            if leaf == "_restore_feature_frame"
        ]
        if len(calls) != 1:
            violations.append("_make_feature_frame must delegate to restoration once")
        elif {keyword.arg for keyword in calls[0].keywords} != {
            "keys",
            "unit",
            "clock_domain",
            "source_identities",
            "columns",
            "origins",
        }:
            violations.append("_make_feature_frame restoration arguments differ")
    return violations


def _slice_2f_materialization_api_violations(tree: ast.Module) -> list[str]:
    """Pin the private byte/frame seam and scalar-only failure provenance."""
    violations: list[str] = []
    aliases = _symbol_aliases(tree)
    expected_signatures = (
        (
            "_raise_feature_computation_failure",
            ["identity_key", "provider_id", "cause"],
            {
                "identity_key": {"str"},
                "provider_id": {"str"},
                "cause": {"Exception"},
            },
            {"typing", "typing.Never"},
        ),
        (
            "_materialize_feature_frame",
            [
                "identity",
                "policy",
                "cost_hints",
                "store",
                "expected_eval_keys",
                "expected_unit",
                "expected_clock_domain",
                "expected_source_identities",
                "expected_column_names",
                "expected_origins",
                "validate_after_lookup",
                "compute",
            ],
            {
                "identity": {f"{ROOT_NAME}.artifacts.identity.StageIdentity"},
                "policy": {"MaterializationPolicy"},
                "cost_hints": {
                    f"{ROOT_NAME}.core.features.CostHint",
                    "tuple",
                },
                "store": {f"{ROOT_NAME}.artifacts.store.ArtifactStore"},
                "expected_eval_keys": {
                    "numpy",
                    "numpy.int64",
                    "numpy.typing",
                    "numpy.typing.NDArray",
                },
                "expected_unit": {f"{ROOT_NAME}.core.time.TimeUnit"},
                "expected_clock_domain": {f"{ROOT_NAME}.core.time.ClockDomain"},
                "expected_source_identities": {
                    f"{ROOT_NAME}.core.identity.DatasetIdentity",
                    "tuple",
                },
                "expected_column_names": {"str", "tuple"},
                "expected_origins": {
                    "collections.abc.Mapping",
                    f"{ROOT_NAME}.core.features.FeatureOrigin",
                    "str",
                },
                "validate_after_lookup": {"collections.abc.Callable"},
                "compute": {
                    "collections.abc.Callable",
                    f"{ROOT_NAME}.core.feature_frames.FeatureFrame",
                },
            },
            {f"{ROOT_NAME}.core.feature_frames.FeatureFrame"},
        ),
        (
            "_requires_persistence",
            ["policy", "cost_hints"],
            {
                "policy": {"MaterializationPolicy"},
                "cost_hints": {
                    f"{ROOT_NAME}.core.features.CostHint",
                    "tuple",
                },
            },
            {"bool"},
        ),
        (
            "_restore_cached_feature_frame",
            [
                "payload",
                "identity_key",
                "expected_eval_keys",
                "expected_unit",
                "expected_clock_domain",
                "expected_source_identities",
                "expected_column_names",
                "expected_origins",
            ],
            {
                "payload": {"bytes"},
                "identity_key": {"str"},
                "expected_eval_keys": {
                    "numpy",
                    "numpy.int64",
                    "numpy.typing",
                    "numpy.typing.NDArray",
                },
                "expected_unit": {f"{ROOT_NAME}.core.time.TimeUnit"},
                "expected_clock_domain": {f"{ROOT_NAME}.core.time.ClockDomain"},
                "expected_source_identities": {
                    f"{ROOT_NAME}.core.identity.DatasetIdentity",
                    "tuple",
                },
                "expected_column_names": {"str", "tuple"},
                "expected_origins": {
                    "collections.abc.Mapping",
                    f"{ROOT_NAME}.core.features.FeatureOrigin",
                    "str",
                },
            },
            {f"{ROOT_NAME}.core.feature_frames.FeatureFrame"},
        ),
    )
    for name, keyword_only, expected_annotations, returns in expected_signatures:
        violations.extend(
            _slice_2f_function_signature_violations(
                tree,
                name=name,
                positional=[],
                keyword_only=keyword_only,
                annotations=expected_annotations,
                returns=returns,
            )
        )

    failures = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "_FeatureComputationError"
    ]
    if len(failures) != 1:
        violations.append(f"expected one _FeatureComputationError, found {len(failures)}")
    else:
        failure = failures[0]
        bases = set().union(
            *(_expression_references(base, aliases) for base in failure.bases),
            set(),
        )
        if bases != {f"{ROOT_NAME}.core.errors.TradingResearchError"}:
            violations.append(f"feature computation error base differs: {bases}")
        slots = [
            member.value
            for member in failure.body
            if isinstance(member, ast.Assign | ast.AnnAssign)
            and "__slots__" in _assigned_names(member)
            and member.value is not None
        ]
        slot_value: object = None
        if len(slots) == 1:
            try:
                slot_value = ast.literal_eval(slots[0])
            except (TypeError, ValueError):
                slot_value = "<non-literal>"
        if slot_value != ("identity_key", "provider_id"):
            violations.append(f"feature computation error slots differ: {slot_value!r}")
        initializer = next(
            (
                member
                for member in failure.body
                if isinstance(member, ast.FunctionDef) and member.name == "__init__"
            ),
            None,
        )
        if initializer is None:
            violations.append("feature computation error lacks one initializer")
        else:
            observed_annotations = {
                argument.arg: _expression_references(argument.annotation, aliases)
                for argument in initializer.args.kwonlyargs
                if argument.annotation is not None
            }
            if (
                initializer.args.posonlyargs
                or [argument.arg for argument in initializer.args.args] != ["self"]
                or [argument.arg for argument in initializer.args.kwonlyargs]
                != ["identity_key", "provider_id"]
                or initializer.args.defaults
                or any(default is not None for default in initializer.args.kw_defaults)
                or initializer.args.vararg is not None
                or initializer.args.kwarg is not None
                or observed_annotations != {"identity_key": {"str"}, "provider_id": {"str"}}
                or not isinstance(initializer.returns, ast.Constant)
                or initializer.returns.value is not None
            ):
                violations.append("feature computation error initializer differs")
            assignments = {
                _qualified_name(node.targets[0]): _qualified_name(node.value)
                for node in initializer.body
                if isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Attribute)
            }
            if assignments != {
                "self.identity_key": "identity_key",
                "self.provider_id": "provider_id",
            }:
                violations.append(f"feature computation error provenance differs: {assignments}")

    materialization_failures = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "_FeatureMaterializationError"
    ]
    if len(materialization_failures) != 1:
        violations.append("materialization must define one private _FeatureMaterializationError")
    else:
        bases = set().union(
            *(_expression_references(base, aliases) for base in materialization_failures[0].bases),
            set(),
        )
        if bases != {f"{ROOT_NAME}.core.errors.TradingResearchError"}:
            violations.append(f"feature materialization error base differs: {bases}")

    raise_helper = _slice_2e_function(tree, "_raise_feature_computation_failure")
    if raise_helper is not None:
        raises = [node for node in ast.walk(raise_helper) if isinstance(node, ast.Raise)]
        if len(raises) != 1:
            violations.append("computation failure helper must raise exactly once")
        else:
            raised = raises[0]
            call = raised.exc
            keywords = (
                {keyword.arg: _qualified_name(keyword.value) for keyword in call.keywords}
                if isinstance(call, ast.Call)
                else {}
            )
            if (
                not isinstance(call, ast.Call)
                or _qualified_name(call.func) != "_FeatureComputationError"
                or call.args
                or keywords
                != {
                    "identity_key": "identity_key",
                    "provider_id": "provider_id",
                }
                or not isinstance(raised.cause, ast.Name)
                or raised.cause.id != "cause"
            ):
                violations.append(
                    "computation failure helper must raise scalar provenance from cause"
                )

    policy = _slice_2e_function(tree, "_requires_persistence")
    if policy is not None:
        references = _expression_references(policy, aliases)
        if any(
            reference.endswith(".EPHEMERAL") or reference.endswith(".PERSIST_ON_FAILURE")
            for reference in references
        ):
            violations.append("nonpersistent policy members must reach no persistence branch")
        expected_policy_members = {
            "MaterializationPolicy.PERSIST",
            "MaterializationPolicy.CACHE_IF_EXPENSIVE",
            f"{ROOT_NAME}.core.features.CostHint.EXPENSIVE",
        }
        observed_policy_members = {
            reference
            for reference in references
            if reference.startswith("MaterializationPolicy.")
            or reference.endswith("CostHint.EXPENSIVE")
        }
        if observed_policy_members != expected_policy_members:
            violations.append(
                f"persistence decision vocabulary differs: {sorted(observed_policy_members)}"
            )
        calls = {leaf for leaf, _line, _call in _function_call_records(policy, aliases)}
        if calls & {"time", "perf_counter", "monotonic"}:
            violations.append("persistence decision consults a clock")
        executable = [
            statement
            for statement in policy.body
            if not (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            )
        ]
        if len(executable) != 5:
            violations.append(
                f"persistence decision must have five exact steps, found {len(executable)}"
            )
        else:
            tuple_test = ast.parse(
                "type(cost_hints) is not tuple or not cost_hints",
                mode="eval",
            ).body
            member_test = ast.parse(
                "any(type(hint) is not _CostHint for hint in cost_hints)",
                mode="eval",
            ).body
            persist_test = ast.parse(
                "policy is MaterializationPolicy.PERSIST",
                mode="eval",
            ).body
            expensive_test = ast.parse(
                "policy is MaterializationPolicy.CACHE_IF_EXPENSIVE",
                mode="eval",
            ).body
            expensive_value = ast.parse(
                "any(hint is _CostHint.EXPENSIVE for hint in cost_hints)",
                mode="eval",
            ).body
            first, second, persist_branch, expensive_branch, fallback = executable
            expensive_return_value = (
                expensive_branch.body[0].value
                if isinstance(expensive_branch, ast.If)
                and len(expensive_branch.body) == 1
                and isinstance(expensive_branch.body[0], ast.Return)
                else None
            )
            if (
                not isinstance(first, ast.If)
                or ast.dump(first.test) != ast.dump(tuple_test)
                or not any(isinstance(node, ast.Raise) for node in first.body)
                or not isinstance(second, ast.If)
                or ast.dump(second.test) != ast.dump(member_test)
                or not any(isinstance(node, ast.Raise) for node in second.body)
                or not isinstance(persist_branch, ast.If)
                or ast.dump(persist_branch.test) != ast.dump(persist_test)
                or len(persist_branch.body) != 1
                or not isinstance(persist_branch.body[0], ast.Return)
                or not isinstance(persist_branch.body[0].value, ast.Constant)
                or persist_branch.body[0].value.value is not True
                or not isinstance(expensive_branch, ast.If)
                or ast.dump(expensive_branch.test) != ast.dump(expensive_test)
                or len(expensive_branch.body) != 1
                or not isinstance(expensive_branch.body[0], ast.Return)
                or expensive_return_value is None
                or ast.dump(expensive_return_value) != ast.dump(expensive_value)
                or not isinstance(fallback, ast.Return)
                or not isinstance(fallback.value, ast.Constant)
                or fallback.value.value is not False
            ):
                violations.append("persistence decision must be PERSIST or any EXPENSIVE hint only")

    materialize = _slice_2e_function(tree, "_materialize_feature_frame")
    if materialize is not None:
        parent_by_child = {
            child: parent
            for parent in ast.walk(materialize)
            for child in ast.iter_child_nodes(parent)
        }
        for callback_name, expected_loads in (
            ("validate_after_lookup", 1),
            ("compute", 2),
        ):
            loads = [
                node
                for node in ast.walk(materialize)
                if isinstance(node, ast.Name)
                and node.id == callback_name
                and isinstance(node.ctx, ast.Load)
            ]
            stores = [
                node
                for node in ast.walk(materialize)
                if isinstance(node, ast.Name)
                and node.id == callback_name
                and isinstance(node.ctx, ast.Store)
            ]
            direct_loads = 0
            for load in loads:
                parent = parent_by_child.get(load)
                if isinstance(parent, ast.Call) and parent.func is load:
                    direct_loads += 1
            if len(loads) != expected_loads or stores or direct_loads != len(loads):
                violations.append(
                    f"{callback_name} must be invoked directly and never aliased or rebound"
                )
        records = _function_call_records(materialize, aliases)
        ordered = {
            leaf: sorted(line for name, line, _call in records if name == leaf)
            for leaf in (
                "_requires_persistence",
                "get",
                "_restore_cached_feature_frame",
                "validate_after_lookup",
                "compute",
                "_encode_feature_artifact",
                "put",
            )
        }
        expected_counts = {
            "_requires_persistence": 1,
            "get": 1,
            "_restore_cached_feature_frame": 1,
            "validate_after_lookup": 1,
            "compute": 2,
            "_encode_feature_artifact": 1,
            "put": 1,
        }
        if any(len(ordered[name]) != count for name, count in expected_counts.items()):
            violations.append(
                f"materialization policy/get/restore/two-compute/encode/put calls differ: {ordered}"
            )
        elif not (
            ordered["_requires_persistence"][0]
            < ordered["compute"][0]
            < ordered["get"][0]
            < ordered["_restore_cached_feature_frame"][0]
            < ordered["validate_after_lookup"][0]
            < ordered["compute"][1]
            < ordered["_encode_feature_artifact"][0]
            < ordered["put"][0]
        ):
            violations.append(f"materialization call order differs: {ordered}")

        state_assignments: dict[str, ast.Assign] = {}
        for statement in materialize.body:
            if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                continue
            only_target = statement.targets[0]
            if isinstance(only_target, ast.Name) and only_target.id in {
                "persist",
                "identity_key",
                "payload",
                "frame",
                "encoded",
            }:
                state_assignments[only_target.id] = statement
        if set(state_assignments) != {
            "persist",
            "identity_key",
            "payload",
            "frame",
            "encoded",
        }:
            violations.append(f"materialization state bindings differ: {sorted(state_assignments)}")
        else:
            persist_value = state_assignments["persist"].value
            payload_value = state_assignments["payload"].value
            frame_value = state_assignments["frame"].value
            encoded_value = state_assignments["encoded"].value
            if (
                not isinstance(persist_value, ast.Call)
                or _qualified_name(persist_value.func) != "_requires_persistence"
                or persist_value.args
                or {
                    keyword.arg: _qualified_name(keyword.value)
                    for keyword in persist_value.keywords
                }
                != {"policy": "policy", "cost_hints": "cost_hints"}
            ):
                violations.append("persist decision must use only policy and cost_hints")
            if (
                not isinstance(payload_value, ast.Call)
                or _qualified_name(payload_value.func) != "store.get"
                or [_qualified_name(argument) for argument in payload_value.args]
                != ["identity_key"]
                or payload_value.keywords
            ):
                violations.append("cache lookup must be store.get(identity_key)")
            if (
                not isinstance(frame_value, ast.Call)
                or _qualified_name(frame_value.func) != "compute"
                or frame_value.args
                or frame_value.keywords
            ):
                violations.append("persistent miss must call compute exactly once")
            if (
                not isinstance(encoded_value, ast.Call)
                or _qualified_name(encoded_value.func) != "_encode_feature_artifact"
                or [_qualified_name(argument) for argument in encoded_value.args]
                != ["identity", "frame"]
                or encoded_value.keywords
            ):
                violations.append("persistent miss must encode identity and frame")

        lookup_callbacks = [
            (index, statement)
            for index, statement in enumerate(materialize.body)
            if isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and _qualified_name(statement.value.func) == "validate_after_lookup"
            and not statement.value.args
            and not statement.value.keywords
        ]
        payload_indices = [
            index
            for index, statement in enumerate(materialize.body)
            if statement is state_assignments.get("payload")
        ]
        hit_indices = [
            index
            for index, statement in enumerate(materialize.body)
            if isinstance(statement, ast.If)
            and ast.dump(statement.test)
            == ast.dump(ast.parse("payload is not None", mode="eval").body)
        ]
        frame_indices = [
            index
            for index, statement in enumerate(materialize.body)
            if statement is state_assignments.get("frame")
        ]
        if (
            len(lookup_callbacks) != 1
            or len(payload_indices) != 1
            or len(hit_indices) != 1
            or len(frame_indices) != 1
            or not (payload_indices[0] < hit_indices[0] < lookup_callbacks[0][0] < frame_indices[0])
        ):
            violations.append(
                "post-lookup validation must be one top-level call after the hit return "
                "and before persistent-miss compute"
            )

        nonpersistent = [
            statement
            for statement in materialize.body
            if isinstance(statement, ast.If)
            and isinstance(statement.test, ast.UnaryOp)
            and isinstance(statement.test.op, ast.Not)
            and isinstance(statement.test.operand, ast.Name)
            and statement.test.operand.id == "persist"
        ]
        if len(nonpersistent) != 1:
            violations.append("materialization must define one nonpersistent branch")
        else:
            body = nonpersistent[0].body
            returned = body[0].value if len(body) == 1 and isinstance(body[0], ast.Return) else None
            if (
                not isinstance(returned, ast.Call)
                or _qualified_name(returned.func) != "compute"
                or returned.args
                or returned.keywords
            ):
                violations.append("nonpersistent branch must only compute and return")

        hits = [
            statement
            for statement in materialize.body
            if isinstance(statement, ast.If)
            and ast.dump(statement.test)
            == ast.dump(ast.parse("payload is not None", mode="eval").body)
        ]
        if len(hits) != 1:
            violations.append("materialization must define one exact cache-hit branch")
        else:
            body = hits[0].body
            returned = body[0].value if len(body) == 1 and isinstance(body[0], ast.Return) else None
            if (
                not isinstance(returned, ast.Call)
                or _qualified_name(returned.func) != "_restore_cached_feature_frame"
                or returned.args
            ):
                violations.append("cache hit must only restore and return")

        put_calls = [call for leaf, _line, call in records if leaf == "put"]
        if len(put_calls) == 1 and (
            [_qualified_name(argument) for argument in put_calls[0].args]
            != ["identity_key", "encoded"]
            or put_calls[0].keywords
        ):
            violations.append("publication must be store.put(identity_key, encoded)")
        for node in ast.walk(materialize):
            if isinstance(node, ast.Try):
                try_calls = {
                    _reference_leaf(reference)
                    for statement in node.body
                    for child in ast.walk(statement)
                    if isinstance(child, ast.Call)
                    for reference in _expression_references(child.func, aliases)
                }
                if "put" in try_calls or any(
                    any(
                        _reference_leaf(reference) == "put"
                        for statement in handler.body
                        for child in ast.walk(statement)
                        if isinstance(child, ast.Call)
                        for reference in _expression_references(child.func, aliases)
                    )
                    for handler in node.handlers
                ):
                    violations.append("store.put is reachable from an exception branch")
    return violations


def _slice_2f_boundary_violations(tree: ast.Module) -> list[str]:
    """Keep policy/restoration free of data authority, clocks and direct I/O."""
    violations: list[str] = []
    imports = _absolute_import_modules(tree)
    if imports != SLICE_2F_MATERIALIZATION_IMPORTS:
        violations.append(
            "materialization imports differ: expected "
            f"{sorted(SLICE_2F_MATERIALIZATION_IMPORTS)}, observed {sorted(imports)}"
        )
    aliases = _symbol_aliases(tree)
    references = {_reference_leaf(reference) for reference in _expression_references(tree, aliases)}
    violations.extend(
        f"materialization references forbidden {name}"
        for name in sorted(references & SLICE_2F_FORBIDDEN_MATERIALIZATION_REFERENCES)
    )
    imported_roots = _imported_module_roots(tree) & (
        CIVIL_TIME_MODULES
        | FORBIDDEN_PUBLIC_TYPE_LIBRARIES
        | {
            "fcntl",
            "os",
            "pathlib",
            "socket",
            "sqlite3",
            "stat",
            "subprocess",
            "tempfile",
            "time",
        }
    )
    violations.extend(
        f"materialization imports forbidden library {name}" for name in sorted(imported_roots)
    )
    for kind, identifier in _identifier_records(tree):
        if "utc" in identifier.lower():
            violations.append(f"materialization {kind} declares UTC name {identifier!r}")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = {
            _reference_leaf(reference) for reference in _expression_references(node.func, aliases)
        }
        violations.extend(
            f"materialization calls forbidden capability {name}"
            for name in sorted(called & SLICE_2F_FORBIDDEN_POLICY_CALLS)
        )
    return violations


def _slice_2f_experiment_violations(tree: ast.Module) -> list[str]:
    """Pin the sole composition root and post-lookup declaration revalidation."""
    violations: list[str] = []
    aliases = _symbol_aliases(tree)
    if _declared_exports(tree) != {"compute_features", "materialize_features"}:
        violations.append(f"experiment exports differ: {sorted(_declared_exports(tree))}")
    violations.extend(
        _slice_2f_function_signature_violations(
            tree,
            name="materialize_features",
            positional=["view", "providers"],
            keyword_only=[
                "split_policy_identity",
                "platform_identity",
                "store",
                "policy",
            ],
            annotations={
                "view": {f"{ROOT_NAME}.data.views.BoundedView"},
                "providers": {
                    "collections.abc.Sequence",
                    f"{ROOT_NAME}.core.interfaces.FeatureProvider",
                },
                "split_policy_identity": {f"{ROOT_NAME}.data.gating.SplitPolicyIdentity"},
                "platform_identity": {f"{ROOT_NAME}.artifacts.identity.PlatformIdentity"},
                "store": {f"{ROOT_NAME}.artifacts.store.ArtifactStore"},
                "policy": {f"{ROOT_NAME}.artifacts.materialization.MaterializationPolicy"},
            },
            returns={f"{ROOT_NAME}.core.feature_frames.FeatureFrame"},
            keyword_defaults={
                "split_policy_identity": None,
                "platform_identity": None,
                "store": None,
                "policy": {
                    f"{ROOT_NAME}.artifacts.materialization.MaterializationPolicy",
                    (
                        f"{ROOT_NAME}.artifacts.materialization."
                        "MaterializationPolicy.CACHE_IF_EXPENSIVE"
                    ),
                },
            },
        )
    )
    materialize = _slice_2e_function(tree, "materialize_features")
    if materialize is None:
        return violations

    records = _function_call_records(materialize, aliases)
    calls_by_leaf: dict[str, list[tuple[int, ast.Call]]] = {}
    for leaf, line, call in records:
        calls_by_leaf.setdefault(leaf, []).append((line, call))
    for leaf, expected_count in {
        "_validated_provider_objects": 1,
        "_validated_plan": 1,
        "_revalidated_provider_plan": 1,
        "_identity_from_plans": 1,
        "_validate_prepared_context": 1,
        "_execute_feature_plans": 1,
        "_materialize_feature_frame": 1,
    }.items():
        observed = len(calls_by_leaf.get(leaf, []))
        if observed != expected_count:
            violations.append(
                f"materialize_features must call {leaf} {expected_count} time(s), "
                f"observed {observed}"
            )

    object_calls = calls_by_leaf.get("_validated_provider_objects", [])
    if object_calls:
        call = object_calls[0][1]
        if (
            len(call.args) != 1
            or call.keywords
            or not isinstance(call.args[0], ast.Name)
            or call.args[0].id != "providers"
        ):
            violations.append("provider detachment must consume raw providers exactly once")
        parent_assignments = [
            node
            for node in materialize.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "provider_objects"
            and node.value is call
        ]
        if len(parent_assignments) != 1:
            violations.append("detached provider sequence must bind once as provider_objects")

    snapshot_calls = calls_by_leaf.get("_validated_plan", [])
    if snapshot_calls:
        call = snapshot_calls[0][1]
        if (
            len(call.args) != 1
            or call.keywords
            or not isinstance(call.args[0], ast.Name)
            or call.args[0].id != "provider_objects"
        ):
            violations.append("initial provider plan must consume only provider_objects")
        parent_assignments = [
            node
            for node in materialize.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "plans"
            and node.value is call
        ]
        if len(parent_assignments) != 1:
            violations.append("initial validated declaration snapshot must bind once as plans")

    providers_loads = [
        node
        for node in ast.walk(materialize)
        if isinstance(node, ast.Name) and node.id == "providers" and isinstance(node.ctx, ast.Load)
    ]
    if len(providers_loads) != 1:
        violations.append(f"raw providers must be read once, observed {len(providers_loads)}")

    def _keyword_name(call: ast.Call, keyword_name: str) -> str | None:
        value = next(
            (keyword.value for keyword in call.keywords if keyword.arg == keyword_name),
            None,
        )
        return value.id if isinstance(value, ast.Name) else None

    for leaf, keyword, expected_name in (
        ("_identity_from_plans", "plans", "plans"),
        ("_validate_prepared_context", "plans", "plans"),
        ("_execute_feature_plans", "plans", "execution_plans"),
    ):
        matches = calls_by_leaf.get(leaf, [])
        if len(matches) == 1 and _keyword_name(matches[0][1], keyword) != expected_name:
            violations.append(f"{leaf} must consume {expected_name}")

    execution_plan_assignments = [
        node
        for node in materialize.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "execution_plans"
        and isinstance(node.value, ast.Name)
        and node.value.id == "plans"
    ]
    if len(execution_plan_assignments) != 1:
        violations.append("execution plans must start from the identity-bound plans snapshot")

    validators = [
        node
        for node in materialize.body
        if isinstance(node, ast.FunctionDef) and node.name == "_validate_after_lookup"
    ]
    if len(validators) != 1:
        violations.append("materialize_features must define one post-lookup validator")
    else:
        validator = validators[0]
        nonlocals = [
            statement
            for statement in validator.body
            if isinstance(statement, ast.Nonlocal) and statement.names == ["execution_plans"]
        ]
        assignments = [
            statement
            for statement in validator.body
            if isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and statement.targets[0].id == "execution_plans"
        ]
        if len(nonlocals) != 1 or len(assignments) != 1:
            violations.append(
                "post-lookup validator must replace the nonlocal execution_plans once"
            )
        else:
            refresh = assignments[0].value
            refresh_keywords = (
                {keyword.arg: _qualified_name(keyword.value) for keyword in refresh.keywords}
                if isinstance(refresh, ast.Call)
                else {}
            )
            if (
                not isinstance(refresh, ast.Call)
                or _qualified_name(refresh.func) != "_revalidated_provider_plan"
                or refresh.args
                or refresh_keywords != {"provider_objects": "provider_objects", "expected": "plans"}
            ):
                violations.append(
                    "post-lookup validator must compare provider_objects with initial plans"
                )

    identity_calls = calls_by_leaf.get("_identity_from_plans", [])
    context_calls = calls_by_leaf.get("_validate_prepared_context", [])
    store_calls = calls_by_leaf.get("_materialize_feature_frame", [])
    if identity_calls and context_calls and identity_calls[0][0] >= context_calls[0][0]:
        violations.append("identity must be constructed before prepared-context refusal")
    if context_calls and store_calls and context_calls[0][0] >= store_calls[0][0]:
        violations.append("prepared context must be validated before store composition")

    identity_builder = _slice_2e_function(tree, "_identity_from_plans")
    if identity_builder is None:
        violations.append("features module must define one _identity_from_plans helper")
    else:
        builder_calls = _function_call_records(identity_builder, aliases)
        stage_calls = [call for leaf, _line, call in builder_calls if leaf == "StageIdentity"]
        if len(stage_calls) != 1:
            violations.append("_identity_from_plans must construct StageIdentity exactly once")
        elif _keyword_name(stage_calls[0], "providers") != "plans":
            violations.append("StageIdentity must consume the validated plans snapshot")
    capture_assignments = [
        node
        for node in materialize.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "capture_provider_failure"
    ]
    if len(capture_assignments) != 1:
        violations.append("materialize_features must derive capture_provider_failure once")
    else:
        value = capture_assignments[0].value
        if (
            not isinstance(value, ast.Compare)
            or len(value.ops) != 1
            or not isinstance(value.ops[0], ast.Is)
            or len(value.comparators) != 1
            or not isinstance(value.left, ast.Name)
            or value.left.id != "policy"
            or _expression_references(value.comparators[0], aliases)
            != {
                f"{ROOT_NAME}.artifacts.materialization.MaterializationPolicy",
                (f"{ROOT_NAME}.artifacts.materialization.MaterializationPolicy.PERSIST_ON_FAILURE"),
            }
        ):
            violations.append("failure capture must be exactly policy is PERSIST_ON_FAILURE")

    for leaf in ("_validate_prepared_context", "_execute_feature_plans"):
        matches = calls_by_leaf.get(leaf, [])
        if len(matches) != 1:
            continue
        capture = next(
            (
                keyword.value
                for keyword in matches[0][1].keywords
                if keyword.arg == "capture_provider_failure"
            ),
            None,
        )
        if not isinstance(capture, ast.Name) or capture.id != "capture_provider_failure":
            violations.append(f"{leaf} must share the exact policy-derived failure-capture flag")

    composition = calls_by_leaf.get("_materialize_feature_frame", [])
    if len(composition) == 1:
        call = composition[0][1]
        keyword_names = {keyword.arg for keyword in call.keywords}
        if call.args or keyword_names != {
            "identity",
            "policy",
            "cost_hints",
            "store",
            "expected_eval_keys",
            "expected_unit",
            "expected_clock_domain",
            "expected_source_identities",
            "expected_column_names",
            "expected_origins",
            "validate_after_lookup",
            "compute",
        }:
            violations.append(
                "materializer composition arguments differ: "
                f"{sorted(name or '**kwargs' for name in keyword_names)}"
            )
        if _keyword_name(call, "identity") != "identity":
            violations.append("materializer must receive the just-built identity")
        if _keyword_name(call, "policy") != "policy":
            violations.append("materializer must receive the exact requested policy")
        if _keyword_name(call, "store") != "store":
            violations.append("materializer must receive the exact explicit store")
        if _keyword_name(call, "compute") != "_compute_prepared":
            violations.append("materializer must receive only the prepared computation")
        if _keyword_name(call, "validate_after_lookup") != "_validate_after_lookup":
            violations.append("materializer must receive only the post-lookup validator")
        cost = next(
            (keyword.value for keyword in call.keywords if keyword.arg == "cost_hints"),
            None,
        )
        if (
            not isinstance(cost, ast.Call)
            or _qualified_name(cost.func) != "tuple"
            or len(cost.args) != 1
            or cost.keywords
            or not isinstance(cost.args[0], ast.GeneratorExp)
            or len(cost.args[0].generators) != 1
            or not isinstance(cost.args[0].generators[0].iter, ast.Name)
            or cost.args[0].generators[0].iter.id != "plans"
            or not isinstance(cost.args[0].elt, ast.Attribute)
            or cost.args[0].elt.attr != "_cost_hint"
        ):
            violations.append("materialization cost hints must derive only from plans")

    direct_store_calls = [leaf for leaf, _line, _call in records if leaf in {"get", "put"}]
    if direct_store_calls:
        violations.append(f"experiment composition accesses store directly: {direct_store_calls}")
    forbidden_calls = {
        leaf
        for leaf, _line, _call in records
        if leaf
        in {
            "_decode_feature_artifact",
            "_encode_feature_artifact",
            "_restore_feature_frame",
            "time",
            "perf_counter",
            "monotonic",
        }
    }
    violations.extend(
        f"experiment composition calls forbidden {name}" for name in sorted(forbidden_calls)
    )

    failure_calls = calls_by_leaf.get("_raise_feature_computation_failure", [])
    if len(failure_calls) != 2:
        violations.append(
            "both context and compute PERSIST_ON_FAILURE paths must attach scalar "
            f"provenance; observed {len(failure_calls)} wrappers"
        )
    else:
        for _line, call in failure_calls:
            keywords = {keyword.arg: keyword.value for keyword in call.keywords}
            if call.args or set(keywords) != {"identity_key", "provider_id", "cause"}:
                violations.append("failure provenance wrapper arguments differ")
                continue
            identity = keywords["identity_key"]
            provider = keywords["provider_id"]
            cause = keywords["cause"]
            if (
                not isinstance(identity, ast.Call)
                or _qualified_name(identity.func) != "identity.identity_key"
                or not isinstance(provider, ast.Attribute)
                or _qualified_name(provider) != "exc.provider_id"
                or not isinstance(cause, ast.Attribute)
                or _qualified_name(cause) != "exc._error"
            ):
                violations.append("failure provenance is not identity/provider scalar-only")
    return violations


def _slice_2g_report_contract_violations(tree: ast.Module) -> list[str]:
    """Pin the five-scalar immutable report and its closed public surface."""
    violations: list[str] = []
    aliases = _symbol_aliases(tree)
    reports = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DeterminismReport"
    ]
    if len(reports) != 1:
        return [f"expected one DeterminismReport, found {len(reports)}"]
    report = reports[0]
    fields = [
        (
            member.target.id,
            _expression_references(member.annotation, aliases),
        )
        for member in report.body
        if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name)
    ]
    expected_fields = [
        ("identity_key", {"str"}),
        ("first_content_hash", {"str"}),
        ("second_content_hash", {"str"}),
        ("structurally_equal", {"bool"}),
        ("deterministic", {"bool"}),
    ]
    if fields != expected_fields:
        violations.append(f"DeterminismReport fields differ: {fields}")
    if _slice_2d_dataclass_options(report, aliases) != [{"frozen": True, "slots": True}]:
        violations.append("DeterminismReport must remain frozen and slotted")
    if _class_public_surface(report) != {name for name, _annotation in expected_fields}:
        violations.append(
            f"DeterminismReport public surface differs: {sorted(_class_public_surface(report))}"
        )

    digest_patterns = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and "_SHA256_HEX" in _assigned_names(node)
        and isinstance(node.value, ast.Call)
    ]
    if len(digest_patterns) != 1:
        violations.append("determinism module must define one SHA-256 report validator")
    else:
        pattern_node = digest_patterns[0].value
        assert isinstance(pattern_node, ast.Call)
        if (
            _qualified_name(pattern_node.func) != "_re.compile"
            or len(pattern_node.args) != 1
            or pattern_node.keywords
            or not isinstance(pattern_node.args[0], ast.Constant)
            or pattern_node.args[0].value != r"^[0-9a-f]{64}$"
        ):
            violations.append("report digest validator must accept lowercase SHA-256 only")
    return violations


def _slice_2g_hash_payload_name(node: ast.AST) -> str | None:
    """Return the payload passed through exact sha256(...).hexdigest()."""
    if (
        not isinstance(node, ast.Call)
        or node.args
        or node.keywords
        or not isinstance(node.func, ast.Attribute)
        or node.func.attr != "hexdigest"
        or not isinstance(node.func.value, ast.Call)
    ):
        return None
    digest = node.func.value
    if (
        _qualified_name(digest.func) != "_hashlib.sha256"
        or len(digest.args) != 1
        or digest.keywords
        or not isinstance(digest.args[0], ast.Name)
    ):
        return None
    return digest.args[0].id


def _slice_2g_verifier_violations(tree: ast.Module) -> list[str]:
    """Pin two forced-ephemeral recomputations and the exact verdict wiring."""
    violations = _slice_2f_function_signature_violations(
        tree,
        name="verify_determinism",
        positional=["identity", "recompute"],
        keyword_only=[],
        annotations={
            "identity": {f"{ROOT_NAME}.artifacts.identity.StageIdentity"},
            "recompute": {
                "collections.abc.Callable",
                f"{ROOT_NAME}.artifacts.materialization.MaterializationPolicy",
                f"{ROOT_NAME}.core.feature_frames.FeatureFrame",
            },
        },
        returns={"DeterminismReport"},
    )
    verifier = _slice_2e_function(tree, "verify_determinism")
    if verifier is None:
        return violations
    aliases = _symbol_aliases(tree)
    records = _function_call_records(verifier, aliases)
    expected_counts = {
        "DeterminismReport": 1,
        "_encode_feature_artifact": 2,
        "_feature_frames_structurally_equal": 1,
        "identity_key": 1,
        "recompute": 2,
    }
    observed_counts = {
        name: sum(leaf == name for leaf, _line, _call in records) for name in expected_counts
    }
    if observed_counts != expected_counts:
        violations.append(f"determinism call inventory differs: {observed_counts}")

    assignments = {
        statement.targets[0].id: statement.value
        for statement in verifier.body
        if isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
    }
    expected_assignment_names = {
        "deterministic",
        "first",
        "first_content_hash",
        "first_payload",
        "second",
        "second_content_hash",
        "second_payload",
        "structurally_equal",
    }
    if set(assignments) != expected_assignment_names:
        violations.append(f"determinism state bindings differ: {sorted(assignments)}")
        return violations

    ephemeral = {
        f"{ROOT_NAME}.artifacts.materialization.MaterializationPolicy",
        f"{ROOT_NAME}.artifacts.materialization.MaterializationPolicy.EPHEMERAL",
    }
    for result_name in ("first", "second"):
        call = assignments[result_name]
        if (
            not isinstance(call, ast.Call)
            or _qualified_name(call.func) != "recompute"
            or len(call.args) != 1
            or call.keywords
            or _expression_references(call.args[0], aliases) != ephemeral
        ):
            violations.append(f"{result_name} must be an independent EPHEMERAL recomputation")

    for payload_name, frame_name in (
        ("first_payload", "first"),
        ("second_payload", "second"),
    ):
        call = assignments[payload_name]
        if (
            not isinstance(call, ast.Call)
            or _qualified_name(call.func) != "_encode_feature_artifact"
            or [_qualified_name(argument) for argument in call.args] != ["identity", frame_name]
            or call.keywords
        ):
            violations.append(f"{payload_name} must bind {frame_name} to the supplied identity")

    for hash_name, payload_name in (
        ("first_content_hash", "first_payload"),
        ("second_content_hash", "second_payload"),
    ):
        if _slice_2g_hash_payload_name(assignments[hash_name]) != payload_name:
            violations.append(f"{hash_name} must hash only {payload_name}")

    structural = assignments["structurally_equal"]
    if (
        not isinstance(structural, ast.Call)
        or _qualified_name(structural.func) != "_feature_frames_structurally_equal"
        or [_qualified_name(argument) for argument in structural.args] != ["first", "second"]
        or structural.keywords
    ):
        violations.append("structural comparison must compare first with second exactly once")
    expected_verdict = ast.parse(
        "structurally_equal and first_payload == second_payload",
        mode="eval",
    ).body
    if ast.dump(assignments["deterministic"]) != ast.dump(expected_verdict):
        violations.append("verdict must require exact structure and direct payload-byte equality")

    returns = [statement for statement in verifier.body if isinstance(statement, ast.Return)]
    if len(returns) != 1 or not isinstance(returns[0].value, ast.Call):
        violations.append("verify_determinism must return one DeterminismReport")
    else:
        report_call = returns[0].value
        keyword_values = {
            keyword.arg: _qualified_name(keyword.value) for keyword in report_call.keywords
        }
        expected_values = {
            "identity_key": None,
            "first_content_hash": "first_content_hash",
            "second_content_hash": "second_content_hash",
            "structurally_equal": "structurally_equal",
            "deterministic": "deterministic",
        }
        identity_value = next(
            (keyword.value for keyword in report_call.keywords if keyword.arg == "identity_key"),
            None,
        )
        if (
            _qualified_name(report_call.func) != "DeterminismReport"
            or report_call.args
            or set(keyword_values) != set(expected_values)
            or any(
                keyword_values[name] != expected
                for name, expected in expected_values.items()
                if name != "identity_key"
            )
            or not isinstance(identity_value, ast.Call)
            or _qualified_name(identity_value.func) != "identity.identity_key"
            or identity_value.args
            or identity_value.keywords
        ):
            violations.append(
                "report must expose the exact identity, hashes, structure and verdict"
            )
    return violations


def _slice_2g_structure_violations(tree: ast.Module) -> list[str]:
    """Pin exact structural fields while keeping float value bits in payload bytes."""
    violations: list[str] = []
    for name, positional, returns in (
        (
            "_feature_frames_structurally_equal",
            ["left", "right"],
            {"bool"},
        ),
        ("_origins_equal", ["left", "right"], {"bool"}),
    ):
        annotations = (
            {
                "left": {f"{ROOT_NAME}.core.feature_frames.FeatureFrame"},
                "right": {f"{ROOT_NAME}.core.feature_frames.FeatureFrame"},
            }
            if name == "_feature_frames_structurally_equal"
            else {
                "left": {f"{ROOT_NAME}.core.features.FeatureOrigin"},
                "right": {f"{ROOT_NAME}.core.features.FeatureOrigin"},
            }
        )
        violations.extend(
            _slice_2f_function_signature_violations(
                tree,
                name=name,
                positional=positional,
                keyword_only=[],
                annotations=annotations,
                returns=returns,
            )
        )

    comparator = _slice_2e_function(tree, "_feature_frames_structurally_equal")
    if comparator is not None:
        observed_body = [
            statement
            for statement in comparator.body
            if not (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            )
        ]
        expected_body = ast.parse(
            """
if type(left) is not _FeatureFrame or type(right) is not _FeatureFrame:
    return False
left_keys = left.keys
right_keys = right.keys
if (
    left_keys.dtype != right_keys.dtype
    or left_keys.shape != right_keys.shape
    or left_keys.tobytes(order="C") != right_keys.tobytes(order="C")
    or left.unit is not right.unit
    or left.clock_domain != right.clock_domain
    or left.source_identities != right.source_identities
    or left.column_names != right.column_names
    or tuple(left.schema.items()) != tuple(right.schema.items())
):
    return False
for name in left.column_names:
    left_values = left.column(name)
    right_values = right.column(name)
    if (
        left_values.dtype != right_values.dtype
        or left_values.shape != right_values.shape
        or _np.isnan(left_values).tobytes(order="C")
        != _np.isnan(right_values).tobytes(order="C")
        or not _origins_equal(left.origin(name), right.origin(name))
    ):
        return False
return True
"""
        ).body
        if [ast.dump(statement) for statement in observed_body] != [
            ast.dump(statement) for statement in expected_body
        ]:
            violations.append(
                "structural comparison must cover exact type, axis, sources, ordered schema, "
                "column dtype/shape, NaN masks and origins"
            )

    origins = _slice_2e_function(tree, "_origins_equal")
    if origins is not None:
        origin_returns = [node for node in ast.walk(origins) if isinstance(node, ast.Return)]
        expected_origin = ast.parse(
            "type(left) is _FeatureOrigin "
            "and type(right) is _FeatureOrigin "
            "and left.provider_id == right.provider_id "
            "and left.version == right.version "
            "and left.window == right.window "
            "and _canonical_params_json(left.params) "
            "== _canonical_params_json(right.params)",
            mode="eval",
        ).body
        if (
            len(origin_returns) != 1
            or origin_returns[0].value is None
            or ast.dump(origin_returns[0].value) != ast.dump(expected_origin)
        ):
            violations.append("origin comparison must use every canonical identity field")
    return violations


def _slice_2g_boundary_violations(tree: ast.Module) -> list[str]:
    """Keep verification cache-free, authority-free, exact and non-ambient."""
    violations: list[str] = []
    imports = _absolute_import_modules(tree)
    if imports != SLICE_2G_DETERMINISM_IMPORTS:
        violations.append(
            "determinism imports differ: expected "
            f"{sorted(SLICE_2G_DETERMINISM_IMPORTS)}, observed {sorted(imports)}"
        )
    if _declared_exports(tree) != SLICE_2G_DETERMINISM_EXPORTS:
        violations.append(f"determinism exports differ: {sorted(_declared_exports(tree))}")
    if _module_level_public_names(tree) != {
        "DeterminismReport",
        "annotations",
        "verify_determinism",
    }:
        violations.append(
            f"determinism public bindings differ: {sorted(_module_level_public_names(tree))}"
        )
    violations.extend(_forbidden_identifier_violations(tree, SLICE_2G_FORBIDDEN_REFERENCES))
    identifiers = {
        identifier
        for _kind, identifier in _identifier_records(tree)
        if identifier in SLICE_2G_FORBIDDEN_IDENTIFIERS
    }
    violations.extend(
        f"determinism declares forbidden tolerance identifier {name}"
        for name in sorted(identifiers)
    )
    aliases = _symbol_aliases(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        references = _expression_references(node.func, aliases)
        forbidden = {
            _reference_leaf(reference)
            for reference in references
            if _reference_leaf(reference) in SLICE_2G_FORBIDDEN_CALLS
            and not (reference == "re.compile" and _reference_leaf(reference) == "compile")
        }
        violations.extend(
            f"determinism calls forbidden capability {name}" for name in sorted(forbidden)
        )
    return violations


def _slice_2c_negative_control_references(tree: ast.Module) -> set[str]:
    """Return exact adversarial-control identifiers present in one source tree."""
    observed: set[str] = set()
    for node in ast.walk(tree):
        candidates: tuple[str, ...] = ()
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            candidates = (node.name,)
        elif isinstance(node, ast.Name):
            candidates = (node.id,)
        elif isinstance(node, ast.Attribute):
            candidates = (node.attr,)
        elif isinstance(node, ast.alias):
            candidates = (
                node.name.rsplit(".", maxsplit=1)[-1],
                node.asname or "",
            )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            candidates = (node.value,)
        observed.update(SLICE_2C_NEGATIVE_CONTROL_CLASSES.intersection(candidates))
    return observed


def test_slice_2a_contract_modules_have_exact_public_bindings() -> None:
    """New contracts expose only the finite ADR-0021 vocabulary."""
    features_path = PACKAGE_ROOT / "core" / "features.py"
    features_tree = ast.parse(features_path.read_text(), filename=str(features_path))
    assert _declared_exports(features_tree) == FEATURES_PUBLIC_EXPORTS
    assert _module_level_public_names(features_tree) == {
        *FEATURES_PUBLIC_EXPORTS,
        "annotations",
    }

    origins = _module_level_binding_origins(features_tree)
    for name, expected in FEATURES_CANONICAL_BINDINGS.items():
        assert origins.get(name) == {expected}, (
            f"core/features.py {name} origin differs: {origins.get(name)}"
        )
    assert origins.get("ParamValue") == PARAM_VALUE_CANONICAL_REFERENCES
    aliases = [
        node
        for node in features_tree.body
        if isinstance(node, ast.TypeAlias)
        and isinstance(node.name, ast.Name)
        and node.name.id == "ParamValue"
    ]
    assert len(aliases) == 1
    alias_references = {
        _reference_leaf(reference) for reference in _expression_references(aliases[0].value, {})
    }
    assert alias_references == PARAM_VALUE_CANONICAL_REFERENCES
    assert not _canonical_runtime_binding_violations(
        f"{ROOT_NAME}.core.features",
        FEATURES_RUNTIME_BINDINGS,
    )

    frames_path = PACKAGE_ROOT / "core" / "feature_frames.py"
    frames_tree = ast.parse(frames_path.read_text(), filename=str(frames_path))
    assert _declared_exports(frames_tree) == {"FeatureFrame"}
    assert not _canonical_source_binding_violations(
        frames_tree,
        FEATURE_FRAMES_CANONICAL_BINDINGS,
    )
    assert not _canonical_runtime_binding_violations(
        f"{ROOT_NAME}.core.feature_frames",
        FEATURE_FRAMES_RUNTIME_BINDINGS,
    )


def test_slice_2a_type_alias_is_visible_to_architecture_guards() -> None:
    """Python 3.13 ``type`` statements cannot escape exact-surface scans."""
    tree = ast.parse("type PublicValue = str | tuple[PublicValue, ...]\n")
    alias = tree.body[0]
    assert isinstance(alias, ast.TypeAlias)
    assert _module_level_public_names(tree) == {"PublicValue"}
    assert _assigned_names(alias) == ("PublicValue",)
    assert _public_annotation_sites(tree)


def test_slice_2a_param_value_guard_detects_final_binding_substitution() -> None:
    """A later assignment cannot silently replace the approved recursive alias."""
    source = (PACKAGE_ROOT / "core" / "features.py").read_text()
    mutated = ast.parse(source + "\nParamValue = str\n")
    assert (
        _module_level_binding_origins(mutated).get("ParamValue") != PARAM_VALUE_CANONICAL_REFERENCES
    )


def test_slice_2a_contract_class_surfaces_are_exact() -> None:
    """Feature carriers stay small and gain no DataFrame conveniences."""
    features_tree = ast.parse((PACKAGE_ROOT / "core" / "features.py").read_text())
    frames_tree = ast.parse((PACKAGE_ROOT / "core" / "feature_frames.py").read_text())
    assert (
        _class_public_surface(_only_class(features_tree, "FeatureRequest"))
        == FEATURE_REQUEST_PUBLIC_SURFACE
    )
    assert (
        _class_public_surface(_only_class(frames_tree, "FeatureFrame"))
        == FEATURE_FRAME_PUBLIC_SURFACE
    )
    assert (
        _class_public_surface(_only_class(features_tree, "WindowSpec")) == WINDOW_SPEC_PUBLIC_FIELDS
    )
    assert (
        _class_public_surface(_only_class(features_tree, "FeatureOrigin"))
        == FEATURE_ORIGIN_PUBLIC_FIELDS
    )


def test_slice_2a_feature_provider_replaces_the_per_point_contract() -> None:
    """The columnar protocol exists once, with no legacy shim or adapter."""
    path = PACKAGE_ROOT / "core" / "interfaces.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    provider = _only_class(tree, "FeatureProvider")
    assert _class_public_surface(provider) == {
        "compute",
        "cost_hint",
        "feature_names",
        "params",
        "provider_id",
        "required_columns",
        "version",
        "window",
    }
    references = {
        _reference_leaf(reference)
        for reference in _expression_references(provider, _symbol_aliases(tree))
    }
    assert {"FeatureRequest", "Mapping", "NDArray", "float64"} <= references
    assert not ({"MarketContext", "FeatureSet"} & references)

    computes = [
        node
        for node in provider.body
        if isinstance(node, ast.FunctionDef) and node.name == "compute"
    ]
    assert len(computes) == 1
    assert [argument.arg for argument in computes[0].args.args] == ["self", "request"]
    assert _mentions_type(
        computes[0].args.args[1].annotation,
        _symbol_aliases(tree),
        "FeatureRequest",
    )


def test_slice_2b_feature_registry_is_retyped_and_has_exact_references() -> None:
    """The typed registry exposes exactly the approved reference providers."""
    path = PACKAGE_ROOT / "features" / "registry.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    references = _expression_references(tree, _symbol_aliases(tree))
    leaves = {_reference_leaf(reference) for reference in references}
    assert {"FeatureProvider", "Registry"} <= leaves
    declarations = [
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "FEATURE_PROVIDERS"
    ]
    assert len(declarations) == 1
    annotation_leaves = {
        _reference_leaf(reference)
        for reference in _expression_references(
            declarations[0].annotation,
            _symbol_aliases(tree),
        )
    }
    assert annotation_leaves == {"FeatureProvider", "Registry"}

    mutated = ast.parse(path.read_text().replace("Registry[FeatureProvider]", "Registry[object]"))
    mutated_declaration = next(
        node
        for node in mutated.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "FEATURE_PROVIDERS"
    )
    mutated_leaves = {
        _reference_leaf(reference)
        for reference in _expression_references(
            mutated_declaration.annotation,
            _symbol_aliases(mutated),
        )
    }
    assert mutated_leaves != {"FeatureProvider", "Registry"}

    module = importlib.import_module(f"{ROOT_NAME}.features.registry")
    assert module.FEATURE_PROVIDERS.names() == frozenset(
        {
            "bar_range",
            "gap_sensitive_levels_h1_v1",
            "log_return_1",
            "multi_confluence_m15_v1",
            "rolling_mean_close",
            "uls_market_context_v1",
            "uls_push_v7",
            "zone_context_v1",
            "zone_trader_v13",
        }
    )


def test_slice_2b_modules_have_exact_public_surfaces() -> None:
    """Execution stays private and 2B adds only the approved providers and adapter."""
    for relative, (exports, public_names) in SLICE_2B_MODULE_SURFACES.items():
        path = PACKAGE_ROOT / relative
        tree = ast.parse(path.read_text(), filename=str(path))
        assert _declared_exports(tree) == exports
        assert _module_level_public_names(tree) == public_names
        assert (
            set(
                _runtime_public_bindings(
                    importlib.import_module(
                        f"{ROOT_NAME}.{relative.removesuffix('.py').replace('/', '.')}"
                    )
                )
            )
            == public_names
        )

    features_init = ast.parse((PACKAGE_ROOT / "features" / "__init__.py").read_text())
    assert _declared_exports(features_init) == {"FEATURE_PROVIDERS"}
    assert _module_level_public_names(features_init) == {"FEATURE_PROVIDERS"}
    experiments_init = ast.parse((PACKAGE_ROOT / "experiments" / "__init__.py").read_text())
    assert not _declared_exports(experiments_init)
    assert not _module_level_public_names(experiments_init)

    experiment_modules = {
        path.relative_to(PACKAGE_ROOT / "experiments").as_posix()
        for path in (PACKAGE_ROOT / "experiments").rglob("*.py")
    }
    assert experiment_modules == {
        "__init__.py",
        "__main__.py",
        "cli.py",
        "config.py",
        "decisions.py",
        "evaluation.py",
        "features.py",
        "fitting.py",
        "folds.py",
        "orchestration.py",
        "outcomes.py",
        "pool.py",
        "resources.py",
        "run_identity.py",
        "runs.py",
        "sweeps.py",
        "training_bundles.py",
        "uls_entry_bundles.py",
        "uls_joint_bundles.py",
        "uls_tp_bundles.py",
        "uls_tp_oof.py",
        "worker.py",
    }


def test_slice_2b_reference_providers_have_the_exact_protocol_surface() -> None:
    """Reference providers implement only the finite columnar provider contract."""
    path = PACKAGE_ROOT / "features" / "reference.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    for name in (
        "BarRange",
        "LogReturn1",
        "RollingMeanClose",
        "UlsMarketContextV1",
        "UlsPushV7",
        "ZoneTraderV13",
    ):
        assert _class_public_surface(_only_class(tree, name)) == SLICE_2B_PROVIDER_SURFACE


def test_slice_2b_adapter_has_one_exact_bounded_view_entry_point() -> None:
    """BoundedView adaptation exists only at composition root and exposes no bypass."""
    path = PACKAGE_ROOT / "experiments" / "features.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "compute_features"
    ]
    assert len(functions) == 1
    function = functions[0]
    assert not function.args.posonlyargs
    assert [argument.arg for argument in function.args.args] == ["view", "providers"]
    assert not function.args.defaults
    assert not function.args.kwonlyargs
    assert function.args.vararg is None
    assert function.args.kwarg is None
    aliases = _symbol_aliases(tree)
    assert function.args.args[0].annotation is not None
    assert function.args.args[1].annotation is not None
    assert function.returns is not None
    assert _expression_references(function.args.args[0].annotation, aliases) == {
        f"{ROOT_NAME}.data.views.BoundedView"
    }
    assert _expression_references(function.args.args[1].annotation, aliases) == {
        "collections.abc.Sequence",
        f"{ROOT_NAME}.core.interfaces.FeatureProvider",
    }
    assert _expression_references(function.returns, aliases) == {
        f"{ROOT_NAME}.core.feature_frames.FeatureFrame"
    }

    for feature_path in _layer_files("features"):
        assert "BoundedView" not in feature_path.read_text(), (
            f"{feature_path.relative_to(PACKAGE_ROOT)} imports the data-layer view"
        )


def test_slice_2b_modules_have_no_io_authority_or_later_stage_capability() -> None:
    """Feature calculation owns arithmetic only; the adapter merely unwraps a view."""
    exact_imports = {
        "features/execution.py": {
            "__future__",
            "collections.abc",
            "dataclasses",
            "numpy",
            "numpy.typing",
            "trading_research.core.errors",
            "trading_research.core.feature_frames",
            "trading_research.core.features",
            "trading_research.core.frames",
            "trading_research.core.interfaces",
            "trading_research.core.time",
            "typing",
        },
        "features/reference.py": {
            "__future__",
            "bisect",
            "collections.abc",
            "dataclasses",
            "math",
            "numpy",
            "numpy.typing",
            "trading_research.core.errors",
            "trading_research.core.features",
            "trading_research.core.time",
            "trading_research.features.registry",
            "typing",
        },
        "experiments/features.py": {
            "__future__",
            "collections.abc",
            "trading_research.artifacts.identity",
            "trading_research.artifacts.materialization",
            "trading_research.artifacts.store",
            "trading_research.core.errors",
            "trading_research.core.feature_frames",
            "trading_research.core.interfaces",
            "trading_research.data.gating",
            "trading_research.data.views",
            "trading_research.features.execution",
            "typing",
        },
    }
    forbidden_identifiers = {
        *RETIRED_RESEARCH_SKELETONS,
        "AccessGrant",
        "DatasetRegistry",
        "DecisionPolicy",
        "FeatureSet",
        "GatedUnlock",
        "MarketFrameLoader",
        "ModelOutput",
        "OutcomeSpec",
        "ResearchModel",
        "TrainingTargetFrame",
        "authorize_access",
        "open_bounded_view",
        "read_split_policy",
    }
    forbidden_calls = {
        "fromfile",
        "getenv",
        "glob",
        "load",
        "memmap",
        "open",
        "read_bytes",
        "read_table",
        "read_text",
        "save",
        "tofile",
        "write_bytes",
        "write_text",
    }
    violations: list[str] = []
    for relative in SLICE_2B_MODULE_SURFACES:
        path = PACKAGE_ROOT / relative
        tree = ast.parse(path.read_text(), filename=str(path))
        aliases = _symbol_aliases(tree)
        imports = _absolute_import_modules(tree)
        if imports != exact_imports[relative]:
            violations.append(
                f"{relative} imports differ: expected {sorted(exact_imports[relative])}, "
                f"observed {sorted(imports)}"
            )
        references = {
            _reference_leaf(reference) for reference in _expression_references(tree, aliases)
        }
        violations.extend(
            f"{relative} references {name}" for name in sorted(references & forbidden_identifiers)
        )
        if relative == "experiments/features.py" and "FEATURE_PROVIDERS" in references:
            violations.append(f"{relative} references FEATURE_PROVIDERS")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = {
                _reference_leaf(reference)
                for reference in _expression_references(node.func, aliases)
            }
            violations.extend(
                f"{relative} calls {name}" for name in sorted(called & forbidden_calls)
            )

            civil_conversions = sorted(_numpy_civil_dtype_conversions(node, aliases))
            if civil_conversions:
                violations.append(
                    f"{relative} performs civil-time dtype conversion {civil_conversions}"
                )

        for identifier_node in ast.walk(tree):
            identifier: str | None = None
            if isinstance(identifier_node, ast.Name):
                identifier = identifier_node.id
            elif isinstance(identifier_node, ast.Attribute):
                identifier = identifier_node.attr
            elif isinstance(identifier_node, ast.arg):
                identifier = identifier_node.arg
            elif isinstance(identifier_node, ast.alias):
                identifier = identifier_node.asname or identifier_node.name
            elif isinstance(
                identifier_node,
                ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
            ):
                identifier = identifier_node.name
            if identifier is not None and "utc" in identifier.lower():
                violations.append(f"{relative} declares UTC identifier {identifier!r}")

        for site, annotation in _public_annotation_sites(tree):
            table_types = sorted(
                reference
                for reference in _expression_references(annotation, aliases)
                if reference.split(".", maxsplit=1)[0] in FORBIDDEN_PUBLIC_TYPE_LIBRARIES
            )
            if table_types:
                violations.append(f"{relative} {site} exposes {table_types}")
    assert not violations, "Slice 2B capability violations: " + "; ".join(violations)


def test_slice_2b_reserved_namespace_predicate_is_exactly_owner_approved() -> None:
    """The V1 reservation is case-sensitive and deliberately not broadened."""
    module = importlib.import_module(f"{ROOT_NAME}.features.execution")
    predicate = module._is_reserved_target_name
    assert all(predicate(name) for name in ("target", "target.value", "target."))
    assert not any(
        predicate(name) for name in ("Target", "TARGET", "targeted", "target_foo", "mytarget.value")
    )


def test_slice_2c_has_exactly_six_test_only_controls_without_registry_mutation() -> None:
    """The adversarial suite is finite and cannot alter production registration."""
    tree = _slice_2c_leakage_tree()
    violations = [
        *_slice_2c_control_inventory_violations(tree),
        *_slice_2c_registry_mutation_violations(tree),
    ]
    assert not violations, "Slice 2C control-boundary violations: " + "; ".join(violations)


def test_slice_2c_leakage_rigs_are_synthetic_and_authority_free() -> None:
    """Release-blocking rigs consume synthetic frames and own no access route."""
    violations = _slice_2c_authority_violations(_slice_2c_leakage_tree())
    assert not violations, "Slice 2C authority violations: " + "; ".join(violations)


def test_slice_2c_leakage_rigs_are_always_active_and_exact() -> None:
    """No marker, expected failure or tolerance may weaken the release gate."""
    tree = _slice_2c_leakage_tree()
    violations = [
        *_slice_2c_gate_evasion_violations(tree),
        *_slice_2c_exact_bit_comparison_violations(tree),
    ]
    assert not violations, "Slice 2C gate-evasion violations: " + "; ".join(violations)


def test_slice_2c_controls_are_absent_from_production_and_live_factories() -> None:
    """Only the approved reference implementations inhabit the live registry."""
    violations: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        references = sorted(_slice_2c_negative_control_references(tree))
        if references:
            violations.append(f"{path.relative_to(PACKAGE_ROOT)} references controls {references}")

    module = importlib.import_module(f"{ROOT_NAME}.features")
    registry = module.FEATURE_PROVIDERS
    if registry.names() != frozenset(SLICE_2C_LIVE_FACTORY_PROVENANCE):
        violations.append(
            "live registry names differ: "
            f"expected {sorted(SLICE_2C_LIVE_FACTORY_PROVENANCE)}, "
            f"observed {sorted(registry.names())}"
        )
    for name, expected in SLICE_2C_LIVE_FACTORY_PROVENANCE.items():
        provider_type = type(registry.create(name))
        observed = (provider_type.__module__, provider_type.__name__)
        if observed != expected:
            violations.append(
                f"live factory {name!r} provenance differs: "
                f"expected {expected}, observed {observed}"
            )
    assert not violations, "Slice 2C production-control violations: " + "; ".join(violations)


def test_slice_2c_architecture_guards_detect_bounded_mutations() -> None:
    """Exercise each finite inventory, authority and gate-evasion boundary."""
    exact_controls = "\n".join(
        f"class {name}:\n    pass" for name in sorted(SLICE_2C_NEGATIVE_CONTROL_CLASSES)
    )
    assert not _slice_2c_control_inventory_violations(ast.parse(exact_controls))
    assert _slice_2c_control_inventory_violations(
        ast.parse(exact_controls.replace("class FutureShiftProvider:", "class FutureLeak:"))
    )

    production_mutation = ast.parse("class FutureShiftProvider:\n    pass\n")
    assert _slice_2c_negative_control_references(production_mutation) == {"FutureShiftProvider"}

    approved_registry_use = ast.parse(
        "from trading_research.features import FEATURE_PROVIDERS\n"
        "LIVE_NAMES = tuple(sorted(FEATURE_PROVIDERS.names()))\n"
        "provider = FEATURE_PROVIDERS.create('bar_range')\n"
    )
    assert not _slice_2c_registry_mutation_violations(approved_registry_use)
    registry_mutations = (
        "from trading_research.features import FEATURE_PROVIDERS\n"
        "FEATURE_PROVIDERS.add('bad', object)\n",
        "from trading_research.features import FEATURE_PROVIDERS\n"
        "@FEATURE_PROVIDERS.register('bad')\n"
        "class Bad:\n    pass\n",
        "from trading_research.features import FEATURE_PROVIDERS\n"
        "FEATURE_PROVIDERS._factories.clear()\n",
    )
    assert all(
        _slice_2c_registry_mutation_violations(ast.parse(mutation))
        for mutation in registry_mutations
    )

    authority_mutations = (
        "from trading_research.data import open_bounded_view\n",
        "from trading_research.core import AccessGrant as _Grant\n",
        "import pathlib\n",
        "import numpy as np\nnp.load('market.npy')\n",
        "import numpy as np\nnp.random.default_rng()\n",
    )
    assert all(
        _slice_2c_authority_violations(ast.parse(mutation)) for mutation in authority_mutations
    )

    gate_evasion_mutations = (
        "import pytest\n@pytest.mark.xfail\ndef test_bad():\n    pass\n",
        "import pytest\n@pytest.mark.skipif(True, reason='bad')\ndef test_bad():\n    pass\n",
        "import pytest\npytest.skip('bad')\n",
        "import pytest\npytest.importorskip('numpy')\n",
        "import pytest\npytestmark = pytest.mark.performance\n",
        "import pytest\nassert actual == pytest.approx(expected)\n",
        "from pytest import skip as _skip\n",
        "import numpy as np\nnp.testing.assert_allclose(actual, expected)\n",
        "assert abs(actual - expected) < 1e-9\n",
    )
    assert all(
        _slice_2c_gate_evasion_violations(ast.parse(mutation))
        for mutation in gate_evasion_mutations
    )

    exact_comparison_source = (
        "import numpy as np\n"
        "def _unrelated_gap_math(keys):\n"
        "    return keys.view(np.uint64)[1:] - keys.view(np.uint64)[:-1]\n"
        "def _assert_array_bits_equal(left, right):\n"
        "    if left.view(np.uint64).tobytes() != "
        "right.view(np.uint64).tobytes():\n"
        "        raise RuntimeError('bits differ')\n"
        "def _assert_frame_values_exact(left, right):\n"
        "    _assert_array_bits_equal(\n"
        "        left.column(name)[left_positions],\n"
        "        right.column(name)[right_positions],\n"
        "    )\n"
    )
    exact_comparison = ast.parse(exact_comparison_source)
    relaxed_comparison = ast.parse(
        exact_comparison_source.replace(
            "    if left.view(np.uint64).tobytes() != right.view(np.uint64).tobytes():\n",
            "    if not np.array_equal(left, right, equal_nan=True):\n",
            1,
        )
    )
    unwired_comparison = ast.parse(
        exact_comparison_source.replace(
            "    _assert_array_bits_equal(\n"
            "        left.column(name)[left_positions],\n"
            "        right.column(name)[right_positions],\n"
            "    )\n",
            "    _ = (left, right)\n",
            1,
        )
    )
    assert not _slice_2c_exact_bit_comparison_violations(exact_comparison)
    assert _slice_2c_exact_bit_comparison_violations(relaxed_comparison)
    assert _slice_2c_exact_bit_comparison_violations(unwired_comparison)


def test_slice_2f_policy_and_public_composition_surfaces_are_exact() -> None:
    """2F adds one policy type and one final experiment entry point."""
    materialization_path = PACKAGE_ROOT / "artifacts" / "materialization.py"
    materialization_tree = ast.parse(
        materialization_path.read_text(),
        filename=str(materialization_path),
    )
    violations = _slice_2f_policy_surface_violations(materialization_tree)
    assert not violations, "Slice 2F policy-surface violations: " + "; ".join(violations)
    assert not _canonical_source_binding_violations(
        materialization_tree,
        SLICE_2F_MATERIALIZATION_SOURCE_BINDINGS,
    )
    assert not _canonical_runtime_binding_violations(
        f"{ROOT_NAME}.artifacts.materialization",
        SLICE_2F_MATERIALIZATION_RUNTIME_BINDINGS,
    )
    policy_module = importlib.import_module(f"{ROOT_NAME}.artifacts.materialization")
    policy_type = policy_module.MaterializationPolicy
    assert {
        name: member.value for name, member in policy_type.__members__.items()
    } == SLICE_2F_POLICY_MEMBERS

    experiment_path = PACKAGE_ROOT / "experiments" / "features.py"
    experiment_tree = ast.parse(
        experiment_path.read_text(),
        filename=str(experiment_path),
    )
    assert not _canonical_source_binding_violations(
        experiment_tree,
        SLICE_2F_EXPERIMENT_SOURCE_BINDINGS,
    )
    assert not _canonical_runtime_binding_violations(
        f"{ROOT_NAME}.experiments.features",
        SLICE_2F_EXPERIMENT_RUNTIME_BINDINGS,
    )


def test_slice_2f_qualification_is_bounded_synthetic_and_always_active() -> None:
    """2F evidence owns no data authority and cannot silently leave the gate."""
    assert SLICE_2F_TEST_PATH.is_file()
    tree = ast.parse(
        SLICE_2F_TEST_PATH.read_text(),
        filename=str(SLICE_2F_TEST_PATH),
    )
    assert _absolute_import_modules(tree) == SLICE_2F_ALLOWED_TEST_IMPORTS
    references = {
        _reference_leaf(reference)
        for reference in _expression_references(tree, _symbol_aliases(tree))
    }
    assert not references & {
        "AccessGrant",
        "DatasetRegistry",
        "GatedUnlock",
        "MarketFrameLoader",
        "authorize_access",
        "open_bounded_view",
        "read_manifest",
        "read_split_policy",
    }
    gate_violations = _slice_2c_gate_evasion_violations(tree)
    assert not gate_violations, "Slice 2F qualification can evade or relax the gate: " + "; ".join(
        gate_violations
    )


def test_slice_2f_revalidates_provider_declarations_after_persistent_miss() -> None:
    """Identity uses one plan; a miss executes only an equivalent fresh plan."""
    execution_path = PACKAGE_ROOT / "features" / "execution.py"
    execution_tree = ast.parse(
        execution_path.read_text(),
        filename=str(execution_path),
    )
    violations = _slice_2f_provider_snapshot_violations(execution_tree)
    assert not violations, "Slice 2F provider-snapshot violations: " + "; ".join(violations)

    experiment_path = PACKAGE_ROOT / "experiments" / "features.py"
    experiment_tree = ast.parse(
        experiment_path.read_text(),
        filename=str(experiment_path),
    )
    violations = _slice_2f_experiment_violations(experiment_tree)
    assert not violations, "Slice 2F experiment-composition violations: " + "; ".join(violations)


def test_slice_2f_materialization_is_clock_free_private_and_failure_safe() -> None:
    """Only canonical feature bytes cross the store boundary after success."""
    path = PACKAGE_ROOT / "artifacts" / "materialization.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    violations = [
        *_slice_2f_materialization_api_violations(tree),
        *_slice_2f_boundary_violations(tree),
    ]
    assert not violations, "Slice 2F materialization violations: " + "; ".join(violations)


def test_slice_2f_cached_frames_use_one_private_validating_restore_seam() -> None:
    """Cache hits cannot construct or expose FeatureFrame through another path."""
    core_path = PACKAGE_ROOT / "core" / "feature_frames.py"
    core_tree = ast.parse(core_path.read_text(), filename=str(core_path))
    violations = _slice_2f_restore_factory_violations(core_tree)
    assert not violations, "Slice 2F restore-factory violations: " + "; ".join(violations)

    materialization_path = PACKAGE_ROOT / "artifacts" / "materialization.py"
    materialization_tree = ast.parse(
        materialization_path.read_text(),
        filename=str(materialization_path),
    )
    restore = _slice_2e_function(
        materialization_tree,
        "_restore_cached_feature_frame",
    )
    assert restore is not None
    aliases = _symbol_aliases(materialization_tree)
    records = _function_call_records(restore, aliases)
    decode = [record for record in records if record[0] == "_decode_feature_artifact"]
    mint = [record for record in records if record[0] == "_restore_feature_frame"]
    assert len(decode) == len(mint) == 1
    assert decode[0][1] < mint[0][1]
    comparisons = [
        node
        for node in ast.walk(restore)
        if isinstance(node, ast.Call)
        and "numpy.array_equal" in _expression_references(node.func, aliases)
    ]
    assert len(comparisons) == 1
    assert [_qualified_name(argument) for argument in comparisons[0].args] == [
        "decoded_keys",
        "expected_eval_keys",
    ]
    assert not comparisons[0].keywords


def test_slice_2f_architecture_guards_detect_bounded_mutations() -> None:
    """Exercise closed policy, snapshot, clock, store-order and public seams."""
    materialization_path = PACKAGE_ROOT / "artifacts" / "materialization.py"
    materialization_source = materialization_path.read_text()
    exact_materialization = ast.parse(materialization_source)
    assert not _slice_2f_policy_surface_violations(exact_materialization)
    assert not _slice_2f_materialization_api_violations(exact_materialization)
    assert not _slice_2f_boundary_violations(exact_materialization)

    policy_mutations = (
        materialization_source.replace(
            '    EPHEMERAL = "ephemeral"',
            '    EPHEMERAL = "memory"',
            1,
        ),
        materialization_source.replace(
            '    PERSIST_ON_FAILURE = "persist_on_failure"',
            '    PERSIST_ON_FAILURE = "persist_on_failure"\n    AUTO = "auto"',
            1,
        ),
    )
    assert all(
        _slice_2f_policy_surface_violations(ast.parse(mutation)) for mutation in policy_mutations
    )
    clock_mutation = ast.parse(materialization_source + "\nimport time\ntime.monotonic()\n")
    assert _slice_2f_boundary_violations(clock_mutation)
    extra_public = ast.parse(materialization_source + "\ndef cache_key():\n    pass\n")
    assert _slice_2f_policy_surface_violations(extra_public)
    write_in_failure = ast.parse(
        materialization_source.replace(
            "    frame = compute()\n",
            "    try:\n        frame = compute()\n"
            "    except Exception:\n        store.put(identity_key, b'failure')\n"
            "        raise\n",
            1,
        )
    )
    assert _slice_2f_materialization_api_violations(write_in_failure)
    cheap_only = ast.parse(
        materialization_source.replace(
            "        return any(hint is _CostHint.EXPENSIVE for hint in cost_hints)",
            "        return False",
            1,
        )
    )
    assert _slice_2f_materialization_api_violations(cheap_only)
    failure_persistence = ast.parse(
        materialization_source.replace(
            "    return False\n\n\ndef _restore_cached_feature_frame",
            "    return policy is MaterializationPolicy.PERSIST_ON_FAILURE\n\n\n"
            "def _restore_cached_feature_frame",
            1,
        )
    )
    assert _slice_2f_materialization_api_violations(failure_persistence)
    inverted_nonpersistent = ast.parse(
        materialization_source.replace("    if not persist:\n", "    if persist:\n", 1)
    )
    assert _slice_2f_materialization_api_violations(inverted_nonpersistent)
    raw_frame_write = ast.parse(
        materialization_source.replace(
            "    store.put(identity_key, encoded)",
            "    store.put(identity_key, frame)",
            1,
        )
    )
    assert _slice_2f_materialization_api_violations(raw_frame_write)
    corruption_to_miss = ast.parse(
        materialization_source.replace(
            "    if payload is not None:\n        return _restore_cached_feature_frame(",
            "    if payload is not None:\n"
            "        try:\n"
            "            return _restore_cached_feature_frame(",
            1,
        ).replace(
            "            expected_origins=expected_origins,\n"
            "        )\n\n"
            "    validate_after_lookup()\n"
            "    frame = compute()",
            "                expected_origins=expected_origins,\n"
            "            )\n"
            "        except Exception:\n"
            "            pass\n\n"
            "    validate_after_lookup()\n"
            "    frame = compute()",
            1,
        )
    )
    assert _slice_2f_materialization_api_violations(corruption_to_miss)
    wrong_order = ast.parse(
        materialization_source.replace(
            "    frame = compute()\n"
            "    encoded = _encode_feature_artifact(identity, frame)\n"
            "    store.put(identity_key, encoded)\n",
            "    encoded = _encode_feature_artifact(identity, compute())\n"
            "    store.put(identity_key, encoded)\n"
            "    frame = compute()\n",
            1,
        )
    )
    assert _slice_2f_materialization_api_violations(wrong_order)
    missing_post_lookup_validation = ast.parse(
        materialization_source.replace("    validate_after_lookup()\n", "", 1)
    )
    assert _slice_2f_materialization_api_violations(missing_post_lookup_validation)
    validation_before_lookup = ast.parse(
        materialization_source.replace("    validate_after_lookup()\n", "", 1).replace(
            "    payload = store.get(identity_key)\n",
            "    validate_after_lookup()\n    payload = store.get(identity_key)\n",
            1,
        )
    )
    assert _slice_2f_materialization_api_violations(validation_before_lookup)
    rebound_validation = ast.parse(
        materialization_source.replace(
            "    validate_after_lookup()\n",
            "    validate_after_lookup = lambda: None\n    validate_after_lookup()\n",
            1,
        )
    )
    assert _slice_2f_materialization_api_violations(rebound_validation)
    aliased_early_compute = ast.parse(
        materialization_source.replace(
            "    validate_after_lookup()\n",
            "    invoke = compute\n    invoke()\n    validate_after_lookup()\n",
            1,
        )
    )
    assert _slice_2f_materialization_api_violations(aliased_early_compute)

    execution_path = PACKAGE_ROOT / "features" / "execution.py"
    execution_source = execution_path.read_text()
    exact_execution = ast.parse(execution_source)
    assert not _slice_2f_provider_snapshot_violations(exact_execution)
    dropped_hint = ast.parse(execution_source.replace("    _cost_hint: _CostHint\n", "", 1))
    assert _slice_2f_provider_snapshot_violations(dropped_hint)
    mutable_hint = ast.parse(
        execution_source.replace(
            "        return self._cost_hint",
            "        return _CostHint.CHEAP",
            1,
        )
    )
    assert _slice_2f_provider_snapshot_violations(mutable_hint)
    provider_reread = ast.parse(
        execution_source.replace(
            '    """Execute one already-snapshotted provider plan without rereading providers."""',
            '    """Execute one already-snapshotted provider plan."""\n'
            "    _snapshot_provider(providers[0], index=0)",
            1,
        )
    )
    assert _slice_2f_provider_snapshot_violations(provider_reread)
    second_sequence_consumption = ast.parse(
        execution_source.replace(
            "    provider_objects = tuple(providers)\n",
            "    provider_objects = tuple(providers)\n    list(providers)\n",
            1,
        )
    )
    assert _slice_2f_provider_snapshot_violations(second_sequence_consumption)
    returned_raw_sequence = ast.parse(
        execution_source.replace("    return provider_objects\n", "    return providers\n", 1)
    )
    assert _slice_2f_provider_snapshot_violations(returned_raw_sequence)
    wrong_resnapshot_source = ast.parse(
        execution_source.replace(
            "    observed = _validated_plan_from_objects(provider_objects)",
            "    observed = _validated_plan_from_objects(expected)",
            1,
        )
    )
    assert _slice_2f_provider_snapshot_violations(wrong_resnapshot_source)
    self_comparison = ast.parse(
        execution_source.replace(
            "_same_provider_declaration(before, after)",
            "_same_provider_declaration(before, before)",
            1,
        )
    )
    assert _slice_2f_provider_snapshot_violations(self_comparison)
    inverted_comparison = ast.parse(
        execution_source.replace(
            "if not _same_provider_declaration(before, after):",
            "if _same_provider_declaration(before, after):",
            1,
        )
    )
    assert _slice_2f_provider_snapshot_violations(inverted_comparison)

    experiment_path = PACKAGE_ROOT / "experiments" / "features.py"
    experiment_source = experiment_path.read_text()
    exact_experiment = ast.parse(experiment_source)
    assert not _slice_2f_experiment_violations(exact_experiment)
    identity_reread = ast.parse(
        experiment_source.replace("        providers=plans,", "        providers=providers,", 1)
    )
    assert _slice_2f_experiment_violations(identity_reread)
    cost_reread = ast.parse(
        experiment_source.replace(
            "cost_hints=tuple(plan._cost_hint for plan in plans)",
            "cost_hints=tuple(provider.cost_hint for provider in providers)",
            1,
        )
    )
    assert _slice_2f_experiment_violations(cost_reread)
    no_capture = ast.parse(
        experiment_source.replace(
            "capture_provider_failure = policy is _MaterializationPolicy.PERSIST_ON_FAILURE",
            "capture_provider_failure = False",
            1,
        )
    )
    assert _slice_2f_experiment_violations(no_capture)
    wrong_revalidation_baseline = ast.parse(
        experiment_source.replace(
            "            expected=plans,",
            "            expected=execution_plans,",
            1,
        )
    )
    assert _slice_2f_experiment_violations(wrong_revalidation_baseline)
    stale_execution_plan = ast.parse(
        experiment_source.replace(
            "                plans=execution_plans,",
            "                plans=plans,",
            1,
        )
    )
    assert _slice_2f_experiment_violations(stale_execution_plan)
    omitted_validation_callback = ast.parse(
        experiment_source.replace(
            "        validate_after_lookup=_validate_after_lookup,\n",
            "",
            1,
        )
    )
    assert _slice_2f_experiment_violations(omitted_validation_callback)

    core_path = PACKAGE_ROOT / "core" / "feature_frames.py"
    core_source = core_path.read_text()
    exact_core = ast.parse(core_source)
    assert not _slice_2f_restore_factory_violations(exact_core)
    public_restore = ast.parse(
        core_source.replace("def _restore_feature_frame(", "def restore_feature_frame(", 1)
    )
    assert _slice_2f_restore_factory_violations(public_restore)


def test_slice_2g_public_report_and_verifier_contracts_are_exact() -> None:
    """Verification exposes only one immutable report and one explicit operation."""
    tree = _slice_2e_module_tree("determinism.py")
    violations = [
        *_slice_2g_report_contract_violations(tree),
        *_slice_2g_verifier_violations(tree),
        *_slice_2g_structure_violations(tree),
        *_slice_2g_boundary_violations(tree),
    ]
    assert not violations, "Slice 2G determinism violations: " + "; ".join(violations)
    assert not _canonical_source_binding_violations(
        tree,
        SLICE_2G_DETERMINISM_SOURCE_BINDINGS,
    )
    assert not _canonical_runtime_binding_violations(
        f"{ROOT_NAME}.artifacts.determinism",
        SLICE_2G_DETERMINISM_RUNTIME_BINDINGS,
    )

    experiment_path = PACKAGE_ROOT / "experiments" / "features.py"
    experiment_tree = ast.parse(
        experiment_path.read_text(),
        filename=str(experiment_path),
    )
    assert _declared_exports(experiment_tree) == {"compute_features", "materialize_features"}
    assert not _canonical_source_binding_violations(
        experiment_tree,
        SLICE_2F_EXPERIMENT_SOURCE_BINDINGS,
    )

    assert SLICE_2G_TEST_PATH.is_file()
    test_tree = ast.parse(
        SLICE_2G_TEST_PATH.read_text(),
        filename=str(SLICE_2G_TEST_PATH),
    )
    assert _absolute_import_modules(test_tree) == SLICE_2G_ALLOWED_TEST_IMPORTS
    assert not _slice_2c_gate_evasion_violations(test_tree)


def test_slice_2g_guards_detect_bounded_cache_and_comparison_mutations() -> None:
    """Exercise force, independence, identity binding, hashing and exact structure."""
    path = PACKAGE_ROOT / "artifacts" / "determinism.py"
    source = path.read_text()
    exact = ast.parse(source, filename=str(path))
    assert not _slice_2g_report_contract_violations(exact)
    assert not _slice_2g_verifier_violations(exact)
    assert not _slice_2g_structure_violations(exact)
    assert not _slice_2g_boundary_violations(exact)

    report_mutations = (
        source.replace(
            "    deterministic: bool\n",
            "    deterministic: bool\n    details: str\n",
            1,
        ),
        source.replace(
            "@_dataclass(frozen=True, slots=True)\nclass DeterminismReport:",
            "@_dataclass(frozen=False, slots=True)\nclass DeterminismReport:",
            1,
        ),
        source.replace(r'r"^[0-9a-f]{64}$"', r'r"^[0-9A-Fa-f]{64}$"', 1),
    )
    assert all(
        mutation != source and _slice_2g_report_contract_violations(ast.parse(mutation))
        for mutation in report_mutations
    )

    verifier_mutations = (
        source.replace(
            "    second = recompute(_MaterializationPolicy.EPHEMERAL)",
            "    second = first",
            1,
        ),
        source.replace(
            "    second = recompute(_MaterializationPolicy.EPHEMERAL)",
            "    second = recompute(_MaterializationPolicy.PERSIST)",
            1,
        ),
        source.replace(
            "    second_payload = _encode_feature_artifact(identity, second)",
            "    second_payload = _encode_feature_artifact(identity, first)",
            1,
        ),
        source.replace(
            "    second_content_hash = _hashlib.sha256(second_payload).hexdigest()",
            "    second_content_hash = _hashlib.sha256(first_payload).hexdigest()",
            1,
        ),
        source.replace(
            "_feature_frames_structurally_equal(first, second)",
            "_feature_frames_structurally_equal(first, first)",
            1,
        ),
        source.replace(
            "structurally_equal and first_payload == second_payload",
            "first_content_hash == second_content_hash",
            1,
        ),
        source.replace(
            "        deterministic=deterministic,",
            "        deterministic=True,",
            1,
        ),
    )
    assert all(
        mutation != source and _slice_2g_verifier_violations(ast.parse(mutation))
        for mutation in verifier_mutations
    )

    structural_mutations = (
        source.replace("        or left.column_names != right.column_names\n", "", 1),
        source.replace(
            "        or tuple(left.schema.items()) != tuple(right.schema.items())\n",
            "",
            1,
        ),
        source.replace(
            "        or left.source_identities != right.source_identities\n",
            "",
            1,
        ),
        source.replace(
            '            or _np.isnan(left_values).tobytes(order="C")\n'
            '            != _np.isnan(right_values).tobytes(order="C")\n',
            "",
            1,
        ),
        source.replace(
            "            or not _origins_equal(left.origin(name), right.origin(name))\n",
            "",
            1,
        ),
        source.replace("_np.isnan(left_values)", "_np.isclose(left_values, right_values)", 1),
    )
    assert all(
        mutation != source and _slice_2g_structure_violations(ast.parse(mutation))
        for mutation in structural_mutations
    )

    boundary_mutations = (
        source + f"\nfrom {ROOT_NAME}.artifacts.store import ArtifactStore\n",
        source + "\ndef _cache_escape(store, key):\n    return store.get(key)\n",
        source + "\ndef _io_escape():\n    return open('determinism.json')\n",
        source + "\nimport random\nrandom.random()\n",
        source + "\ndef compare(left, right, *, rtol=1e-9):\n    return left == right\n",
    )
    assert all(
        _slice_2g_boundary_violations(ast.parse(mutation)) for mutation in boundary_mutations
    )


def test_slice_2g_modules_have_exact_inventory_exports_and_bindings() -> None:
    """Artifacts add only the approved verifier beside the settled 2F surface."""
    artifacts_root = PACKAGE_ROOT / "artifacts"
    observed_inventory = {
        path.relative_to(artifacts_root).as_posix() for path in artifacts_root.rglob("*.py")
    }
    assert observed_inventory == SLICE_2G_MODULE_INVENTORY

    identity_tree = _slice_2d_identity_tree()
    assert _declared_exports(identity_tree) == SLICE_2D_PUBLIC_EXPORTS
    assert not _canonical_source_binding_violations(
        identity_tree,
        SLICE_2D_IDENTITY_SOURCE_BINDINGS,
    )
    assert not _canonical_runtime_binding_violations(
        f"{ROOT_NAME}.artifacts.identity",
        SLICE_2D_IDENTITY_RUNTIME_BINDINGS,
    )

    package_path = artifacts_root / "__init__.py"
    package_tree = ast.parse(package_path.read_text(), filename=str(package_path))
    assert _declared_exports(package_tree) == SLICE_2G_PUBLIC_EXPORTS
    assert not _canonical_source_binding_violations(
        package_tree,
        SLICE_2G_PACKAGE_SOURCE_BINDINGS,
    )
    assert not _canonical_runtime_binding_violations(
        f"{ROOT_NAME}.artifacts",
        SLICE_2G_PACKAGE_RUNTIME_BINDINGS,
    )

    container_tree = _slice_2e_module_tree("container.py")
    determinism_tree = _slice_2e_module_tree("determinism.py")
    fitted_tree = _slice_2e_module_tree("fitted.py")
    materialization_tree = _slice_2e_module_tree("materialization.py")
    store_tree = _slice_2e_module_tree("store.py")
    assert _declared_exports(container_tree) == set()
    assert _declared_exports(store_tree) == {"ArtifactStore"}
    assert not _canonical_source_binding_violations(
        store_tree,
        SLICE_2E_STORE_SOURCE_BINDINGS,
    )
    assert not _canonical_runtime_binding_violations(
        f"{ROOT_NAME}.artifacts.store",
        SLICE_2E_STORE_RUNTIME_BINDINGS,
    )

    dynamic_violations = {
        relative: sorted(_dynamic_public_attribute_definitions(tree))
        for relative, tree in (
            ("artifacts/__init__.py", package_tree),
            ("artifacts/container.py", container_tree),
            ("artifacts/determinism.py", determinism_tree),
            ("artifacts/fitted.py", fitted_tree),
            ("artifacts/identity.py", identity_tree),
            ("artifacts/materialization.py", materialization_tree),
            ("artifacts/store.py", store_tree),
        )
        if _dynamic_public_attribute_definitions(tree)
    }
    assert not dynamic_violations, (
        f"Slice 2G modules define dynamic public hooks: {dynamic_violations}"
    )


def test_slice_2d_public_classes_and_stage_signature_are_exact() -> None:
    """Identity construction stays explicit, keyword-only and minimal."""
    violations = _slice_2d_class_contract_violations(_slice_2d_identity_tree())
    assert not violations, "Slice 2D class-contract violations: " + "; ".join(violations)


def test_slice_2d_identity_is_pure_and_canonically_hashed() -> None:
    """The key path has no ambient authority and one exact canonical encoding."""
    tree = _slice_2d_identity_tree()
    violations = [
        *_slice_2d_purity_violations(tree),
        *_slice_2d_encoding_violations(tree),
    ]
    assert not violations, "Slice 2D identity-boundary violations: " + "; ".join(violations)


def test_slice_2e_identity_retains_one_private_canonical_preimage() -> None:
    """Artifact provenance reuses the exact key preimage without public widening."""
    violations = _slice_2e_identity_snapshot_violations(_slice_2d_identity_tree())
    assert not violations, "Slice 2E identity-snapshot violations: " + "; ".join(violations)


def test_slice_2d_architecture_guards_detect_bounded_mutations() -> None:
    """Exercise surface, signature, authority, encoding and hash regressions."""
    identity_path = PACKAGE_ROOT / "artifacts" / "identity.py"
    source = identity_path.read_text()
    exact = ast.parse(source, filename=str(identity_path))
    assert not _slice_2d_class_contract_violations(exact)
    assert not _slice_2d_purity_violations(exact)
    assert not _slice_2d_encoding_violations(exact)

    platform_field_mutation = ast.parse(
        source.replace(
            "    diff_sha256: str | None\n",
            "    diff_sha256: str | None\n    hostname: str\n",
            1,
        )
    )
    positional_mutation = ast.parse(
        source.replace(
            "        *,\n        source_identities:",
            "        source_identities:",
            1,
        )
    )
    renamed_method_mutation = ast.parse(source.replace("    def identity_key(", "    def key(", 1))
    mutable_platform_mutation = ast.parse(
        source.replace(
            "@_dataclass(frozen=True, slots=True)\nclass PlatformIdentity:",
            "@_dataclass(frozen=False, slots=True)\nclass PlatformIdentity:",
            1,
        )
    )
    assert all(
        _slice_2d_class_contract_violations(mutated)
        for mutated in (
            platform_field_mutation,
            positional_mutation,
            renamed_method_mutation,
            mutable_platform_mutation,
        )
    )

    purity_mutations = (
        source + "\nimport os\n",
        source + "\ndef _io_escape():\n    return open('identity.txt')\n",
        source + "\ndef _ambient_escape():\n    return __file__\n",
        source + "\ndef _cwd_escape():\n    return os.getcwd()\n",
        source + "\ndef _salted_escape(value):\n    return hash(value)\n",
        source + "\ndef _repr_escape(value):\n    return repr(value)\n",
    )
    assert all(_slice_2d_purity_violations(ast.parse(mutated)) for mutated in purity_mutations)

    encoding_mutations = (
        source.replace("sort_keys=True", "sort_keys=False", 1),
        source.replace("ensure_ascii=True", "ensure_ascii=False", 1),
        source.replace('separators=(",", ":")', 'separators=(", ", ": ")', 1),
        source.replace("_hashlib.sha256", "_hashlib.sha1", 1),
        source.replace('canonical.encode("utf-8")', 'canonical.encode("utf-16")', 1),
        source.replace(
            '"params": _json.loads(_canonical_params_json(origin.params)),',
            '"params": dict(origin.params),',
            1,
        ),
    )
    assert all(_slice_2d_encoding_violations(ast.parse(mutated)) for mutated in encoding_mutations)

    dynamic_mutation = ast.parse(source + "\ndef __getattr__(name):\n    return name\n")
    assert _dynamic_public_attribute_definitions(dynamic_mutation) == {"__getattr__"}

    package_path = PACKAGE_ROOT / "artifacts" / "__init__.py"
    package_source = package_path.read_text()
    rebound_package = ast.parse(package_source + "\nStageIdentity = PlatformIdentity\n")
    assert _canonical_source_binding_violations(
        rebound_package,
        SLICE_2G_PACKAGE_SOURCE_BINDINGS,
    )
    narrowed_exports = ast.parse(
        package_source.replace(
            '    "MaterializationPolicy",\n',
            "",
            1,
        )
    )
    assert _declared_exports(narrowed_exports) != SLICE_2G_PUBLIC_EXPORTS


def test_slice_2e_store_and_container_surfaces_are_exact() -> None:
    """Only ArtifactStore is public; serialization stays a bounded byte seam."""
    container = _slice_2e_module_tree("container.py")
    store = _slice_2e_module_tree("store.py")
    violations = [
        *_slice_2e_container_api_violations(container),
        *_slice_2e_store_api_violations(store),
    ]
    assert not violations, "Slice 2E API violations: " + "; ".join(violations)


def test_slice_2e_artifacts_obey_exact_dependency_and_authority_budgets() -> None:
    """Container and store see only core contracts and their bounded capabilities."""
    violations = [
        *_slice_2e_boundary_violations(_slice_2e_module_tree("container.py"), module="container"),
        *_slice_2e_boundary_violations(_slice_2e_module_tree("store.py"), module="store"),
    ]
    assert not violations, "Slice 2E boundary violations: " + "; ".join(violations)


def test_slice_2e_container_is_canonical_pickle_free_and_self_verifying() -> None:
    """The uncompressed NPZ embeds and re-hashes one exact stage preimage."""
    violations = _slice_2e_container_format_violations(_slice_2e_module_tree("container.py"))
    assert not violations, "Slice 2E container violations: " + "; ".join(violations)


def test_slice_2e_store_is_descriptor_safe_locked_and_no_replace() -> None:
    """One identity cannot race into two contents or traverse a symlink parent."""
    tree = _slice_2e_module_tree("store.py")
    violations = [
        *_slice_2e_store_durability_violations(tree),
        *_slice_2e_conflict_violations(tree),
    ]
    assert not violations, "Slice 2E durability violations: " + "; ".join(violations)


def test_slice_2e_leaves_the_prior_atomic_writer_byte_exact_and_unreused() -> None:
    """The artifact publisher is local; Phase-1 atomic_write gains no caller."""
    atomic_path = PACKAGE_ROOT / "data" / "atomic_write.py"
    atomic_module = f"{ROOT_NAME}.data.atomic_write"
    observed_digest = hashlib.sha256(atomic_path.read_bytes()).hexdigest()
    assert observed_digest == SLICE_2E_ATOMIC_WRITE_SHA256

    importers: dict[str, tuple[str, ...]] = {}
    references_by_module: dict[str, set[str]] = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if path == atomic_path:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        aliases = _symbol_aliases(tree)
        atomic_references = {
            reference
            for reference in _expression_references(tree, aliases)
            if reference == atomic_module or reference.startswith(f"{atomic_module}.")
        }
        if atomic_references:
            references_by_module[path.relative_to(PACKAGE_ROOT).as_posix()] = atomic_references
        imported_names = tuple(
            imported.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == atomic_module
            for imported in node.names
        )
        direct_import = any(
            imported.name == f"{ROOT_NAME}.data.atomic_write"
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for imported in node.names
        )
        if imported_names or direct_import:
            importers[path.relative_to(PACKAGE_ROOT).as_posix()] = imported_names
    assert importers == {
        "data/views_generation.py": (
            "_ensure_durable_directory",
            "_publish_immutable_bytes",
        )
    }
    assert references_by_module == {
        "data/views_generation.py": {
            f"{atomic_module}._ensure_durable_directory",
            f"{atomic_module}._publish_immutable_bytes",
        }
    }


def test_slice_2e_qualification_modules_are_synthetic_and_authority_free() -> None:
    """Container, corruption and race tests never open a platform data route."""
    violations = _slice_2e_test_import_violations()
    assert not violations, "Slice 2E test-import violations: " + "; ".join(violations)


def test_slice_2e_architecture_guards_detect_bounded_mutations() -> None:
    """Prove the guards catch surface, provenance, NPZ and durability drift."""
    identity_source = (PACKAGE_ROOT / "artifacts" / "identity.py").read_text()
    container_source = (PACKAGE_ROOT / "artifacts" / "container.py").read_text()
    store_source = (PACKAGE_ROOT / "artifacts" / "store.py").read_text()

    identity_mutations = (
        _slice_2e_mutated_tree(
            identity_source,
            "    _canonical_json: str = _field(repr=False, compare=False)\n",
            "",
        ),
        _slice_2e_mutated_tree(
            identity_source,
            "_field(repr=False, compare=False)",
            "_field(repr=True, compare=False)",
        ),
        _slice_2e_mutated_tree(
            identity_source,
            "        return self._canonical_json\n",
            "        return self._identity_key\n",
        ),
        _slice_2e_mutated_tree(
            identity_source,
            '        object.__setattr__(self, "_canonical_json", canonical)\n',
            '        object.__setattr__(self, "_canonical_json", canonical + " ")\n',
        ),
    )
    assert all(_slice_2e_identity_snapshot_violations(tree) for tree in identity_mutations)

    container_mutations = (
        _slice_2e_mutated_tree(
            container_source,
            "_np.savez)(output, **arrays)",
            "_np.savez_compressed)(output, **arrays)",
        ),
        _slice_2e_mutated_tree(
            container_source,
            "def _read_canonical_metadata(payload: bytes) "
            "-> tuple[dict[str, object], tuple[str, ...]]:\n"
            "    try:\n"
            "        with _np.load(_io.BytesIO(payload), allow_pickle=False) as archive:",
            "def _read_canonical_metadata(payload: bytes) "
            "-> tuple[dict[str, object], tuple[str, ...]]:\n"
            "    try:\n"
            "        with _np.load(_io.BytesIO(payload), allow_pickle=True) as archive:",
        ),
        _slice_2e_mutated_tree(
            container_source,
            '_hashlib.sha256(_canonical_json(stage).encode("utf-8"))',
            '_hashlib.sha1(_canonical_json(stage).encode("utf-8"))',
        ),
        _slice_2e_mutated_tree(
            container_source,
            "_validate_frame_binding(frame=frame, binding=binding)",
            "_ = (frame, binding)",
        ),
        _slice_2e_mutated_tree(
            container_source,
            "_zipfile.ZIP_STORED",
            "_zipfile.ZIP_DEFLATED",
        ),
        _slice_2e_mutated_tree(
            container_source,
            "_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)",
            "_ZIP_TIMESTAMP = (1981, 1, 1, 0, 0, 0)",
        ),
        _slice_2e_mutated_tree(
            container_source,
            'with _zipfile.ZipFile(_io.BytesIO(payload), mode="r") as archive:',
            'with _zipfile.ZipFile("artifact.npz", mode="r") as archive:',
        ),
        ast.parse(container_source + "\ndef _dynamic_escape(value):\n    return eval(value)\n"),
    )
    assert all(
        _slice_2e_container_format_violations(tree)
        or _slice_2e_boundary_violations(tree, module="container")
        for tree in container_mutations
    )

    store_mutations = (
        _slice_2e_mutated_tree(
            store_source,
            '    | getattr(_os, "O_DIRECTORY", 0)\n    | getattr(_os, "O_NOFOLLOW", 0)\n',
            '    | getattr(_os, "O_DIRECTORY", 0)\n    | getattr(_os, "O_NOFOLLOW_DISABLED", 0)\n',
        ),
        _slice_2e_mutated_tree(
            store_source,
            "_link_at = _os.link",
            "_link_at = _os.replace",
        ),
        _slice_2e_mutated_tree(
            store_source,
            "_lock_fd = _fcntl.flock",
            "_lock_fd = lambda *_args: None",
        ),
        _slice_2e_mutated_tree(
            store_source,
            '        _sync_directory(identity_fd, label=f"identity {identity_key}")',
            "        _ = identity_fd",
        ),
        _slice_2e_mutated_tree(
            store_source,
            "    _decode_feature_artifact(payload, expected_identity_key=key)\n",
            "",
        ),
        _slice_2e_mutated_tree(
            store_source,
            "    observed_hash = _hashlib.sha256(payload).hexdigest()\n"
            "    if observed_hash != expected_hash:\n"
            "        raise _ArtifactCorruptionError(\n"
            '            f"artifact {name!r} content hashes to {observed_hash}, not its filename"\n'
            "        )\n"
            "    try:\n"
            "        _decode_feature_artifact(payload, expected_identity_key=identity_key)\n"
            "    except _ArtifactContainerError as exc:\n"
            "        raise _ArtifactCorruptionError("
            'f"artifact {name!r} has invalid container bytes") from exc\n',
            "    try:\n"
            "        _decode_feature_artifact(payload, expected_identity_key=identity_key)\n"
            "    except _ArtifactContainerError as exc:\n"
            "        raise _ArtifactCorruptionError("
            'f"artifact {name!r} has invalid container bytes") from exc\n'
            "    observed_hash = _hashlib.sha256(payload).hexdigest()\n"
            "    if observed_hash != expected_hash:\n"
            "        raise _ArtifactCorruptionError(\n"
            '            f"artifact {name!r} content hashes to {observed_hash}, not its filename"\n'
            "        )\n",
        ),
        _slice_2e_mutated_tree(
            store_source,
            "descriptor = _open_fd(name, _READ_FLAGS, dir_fd=identity_fd)",
            "descriptor = _open_fd(name, _DIRECTORY_FLAGS, dir_fd=identity_fd)",
        ),
        _slice_2e_mutated_tree(
            store_source,
            "def _sync_directory(descriptor: int, *, label: str) -> None:\n"
            "    try:\n"
            "        _sync_fd(descriptor)\n",
            "def _sync_directory(descriptor: int, *, label: str) -> None:\n"
            "    try:\n"
            "        _ = descriptor\n",
        ),
        _slice_2e_mutated_tree(
            store_source,
            "                if len(names) != 1:\n"
            "                    raise _ArtifactConflictError(\n",
            "                if len(names) != 1:\n                    raise _ArtifactStoreError(\n",
        ),
        ast.parse(store_source + "\ndef _ambient_delete():\n    _os.remove('other')\n"),
    )
    assert all(
        _slice_2e_store_durability_violations(tree)
        or _slice_2e_conflict_violations(tree)
        or _slice_2e_boundary_violations(tree, module="store")
        for tree in store_mutations
    )

    dynamic_container = ast.parse(container_source + "\ndef __getattr__(name):\n    return name\n")
    public_store = ast.parse(store_source + "\ndef list_artifacts():\n    return ()\n")
    assert _dynamic_public_attribute_definitions(dynamic_container) == {"__getattr__"}
    assert _canonical_source_binding_violations(
        public_store,
        SLICE_2E_STORE_SOURCE_BINDINGS,
    )


def test_slice_2a_contract_path_has_no_io_civil_time_or_table_library() -> None:
    """2A is contracts only: no I/O, authorization, civil time or table API."""
    forbidden_imports = {
        "calendar",
        "dateutil",
        "duckdb",
        "os",
        "pandas",
        "pathlib",
        "polars",
        "pyarrow",
        "pytz",
        "subprocess",
        "time",
        "zoneinfo",
    }
    forbidden_calls = {
        "fromfile",
        "getenv",
        "glob",
        "load",
        "memmap",
        "open",
        "read_bytes",
        "read_text",
        "save",
        "savez",
        "tofile",
        "write_bytes",
        "write_text",
    }
    violations: list[str] = []
    for relative in SLICE_2A_CONTRACT_MODULES:
        path = PACKAGE_ROOT / relative
        tree = ast.parse(path.read_text(), filename=str(path))
        aliases = _symbol_aliases(tree)
        imports = _imported_module_roots(tree) & forbidden_imports
        violations.extend(f"{relative} imports {name}" for name in sorted(imports))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                called = {
                    _reference_leaf(reference)
                    for reference in _expression_references(node.func, aliases)
                }
                violations.extend(
                    f"{relative} calls {name}" for name in sorted(called & forbidden_calls)
                )
            identifier: str | None = None
            if isinstance(node, ast.Name):
                identifier = node.id
            elif isinstance(node, ast.Attribute):
                identifier = node.attr
            elif isinstance(node, ast.arg):
                identifier = node.arg
            elif isinstance(node, ast.alias):
                identifier = node.asname or node.name
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                identifier = node.name
            if identifier is not None and "utc" in identifier.lower():
                violations.append(f"{relative} declares UTC identifier {identifier!r}")
    assert not violations, "Slice 2A boundary violations: " + "; ".join(violations)


def test_slice_3a_retires_per_row_research_carriers_without_a_shim() -> None:
    """Phase-1/2 contracts stay untouched and retired research names stay absent."""
    for relative in ("core/features.py", "core/feature_frames.py", "features/registry.py"):
        source = (PACKAGE_ROOT / relative).read_text()
        assert "FeatureSet" not in source
        assert "MarketContext" not in source
    assert not (PACKAGE_ROOT / "core" / "domain.py").exists()
    core_exports = _declared_exports(ast.parse((PACKAGE_ROOT / "core" / "__init__.py").read_text()))
    registry_source = (PACKAGE_ROOT / "core" / "registry.py").read_text()
    for name in RETIRED_RESEARCH_SKELETONS | RETIRED_RESEARCH_CARRIERS:
        assert name not in core_exports
    assert "from_spec" not in registry_source


def test_slice_3a_modules_export_exactly_the_closed_public_vocabulary() -> None:
    """The Phase-3 contract surface has no placeholder or invented public type."""
    expected = {
        "core/research.py": PHASE3_RESEARCH_EXPORTS,
        "core/research_frames.py": PHASE3_FRAME_EXPORTS,
        "core/interfaces.py": PHASE3_PROTOCOL_EXPORTS,
    }
    for relative, exports in expected.items():
        tree = ast.parse((PACKAGE_ROOT / relative).read_text())
        assert _declared_exports(tree) == exports
        assert _module_level_public_names(tree) == {*exports, "annotations"}
        module_name = relative.removesuffix(".py").replace("/", ".")
        assert set(
            _runtime_public_bindings(importlib.import_module(f"{ROOT_NAME}.{module_name}"))
        ) == {*exports, "annotations"}


def test_slice_3a_value_and_frame_surfaces_are_exact() -> None:
    """Every carrier stays small and no generic frame base is introduced."""
    research_tree = ast.parse((PACKAGE_ROOT / "core" / "research.py").read_text())
    frame_tree = ast.parse((PACKAGE_ROOT / "core" / "research_frames.py").read_text())
    expected_values = {
        "ViewSpan": {
            "clock_domain",
            "context_start",
            "eval_end",
            "eval_start",
            "source_identities",
            "unit",
        },
        "StageOrigin": {"params", "plugin_id", "version"},
        "ToleranceSpec": {"atol", "reason", "rtol"},
        "MetricValue": {"effective_samples", "name", "value"},
        "ModelInputs": {"features", "representation"},
        "CorpusModelInputs": {
            "input",
            "input_names",
            "num_rows",
            "target",
            "target_names",
            "weights",
        },
        "OutcomeRequest": {"column", "decision_length", "decision_offset", "keys"},
    }
    for name, surface in expected_values.items():
        assert _class_public_surface(_only_class(research_tree, name)) == surface

    for name in (
        "RepresentationFrame",
        "ModelOutputFrame",
        "TrainingTargetFrame",
        "OutcomeFrame",
    ):
        node = _only_class(frame_tree, name)
        assert not node.bases, f"{name} must not acquire a generic frame base"
        assert _class_public_surface(node) == PHASE3_DENSE_FRAME_SURFACE
    assert _class_public_surface(_only_class(frame_tree, "IntentFrame")) == {
        "carries_brackets",
        "clock_domain",
        "intent_schema_id",
        "keys",
        "num_rows",
        "stop_loss_distance",
        "take_profit_distance",
        "target_exposure",
        "unit",
    }
    assert _class_public_surface(_only_class(frame_tree, "FittedState")) == {
        "identity_key",
        "payload",
    }
    assert _class_public_surface(_only_class(frame_tree, "FittedPayload")) == {
        "array",
        "array_names",
        "metadata",
    }


def test_slice_3a_protocol_surfaces_and_target_channel_are_structural() -> None:
    """Only fit owns the target channel and infer can never receive it."""
    tree = ast.parse((PACKAGE_ROOT / "core" / "interfaces.py").read_text())
    aliases = _symbol_aliases(tree)
    for name, surface in PHASE3_PROTOCOL_SURFACES.items():
        assert _class_public_surface(_only_class(tree, name)) == surface

    model = _only_class(tree, "ResearchModel")
    fit = next(
        node for node in model.body if isinstance(node, ast.FunctionDef) and node.name == "fit"
    )
    infer = next(
        node for node in model.body if isinstance(node, ast.FunctionDef) and node.name == "infer"
    )
    assert [argument.arg for argument in fit.args.args] == [
        "self",
        "view",
        "inputs",
        "training_targets",
    ]
    assert [argument.arg for argument in infer.args.args] == [
        "self",
        "view",
        "fitted_state",
        "inputs",
    ]
    assert _mentions_type(fit.args.args[-1].annotation, aliases, "TrainingTargetFrame")
    assert _mentions_type(fit.returns, aliases, "FittedPayload")
    assert not _mentions_type(fit.returns, aliases, "FittedState")
    assert not any(
        _mentions_type(annotation, aliases, "TrainingTargetFrame")
        for _, annotation in _function_annotation_sites("ResearchModel.infer", infer)
    )


def test_slice_3a_representation_signature_makes_chaining_unrepresentable() -> None:
    """RepresentationModel consumes FeatureFrame directly and no representation bundle."""
    tree = ast.parse((PACKAGE_ROOT / "core" / "interfaces.py").read_text())
    aliases = _symbol_aliases(tree)
    representation = _only_class(tree, "RepresentationModel")
    methods = {
        node.name: node
        for node in representation.body
        if isinstance(node, ast.FunctionDef) and node.name in {"fit", "encode"}
    }
    assert [argument.arg for argument in methods["fit"].args.args] == [
        "self",
        "view",
        "features",
    ]
    assert [argument.arg for argument in methods["encode"].args.args] == [
        "self",
        "view",
        "fitted_state",
        "features",
    ]
    for method in methods.values():
        feature_argument = method.args.args[-1]
        assert _mentions_type(feature_argument.annotation, aliases, "FeatureFrame")
        references = {
            _reference_leaf(reference)
            for _, annotation in _function_annotation_sites(method.name, method)
            for reference in _expression_references(annotation, aliases)
        }
        assert "ModelInputs" not in references
        assert "TrainingTargetFrame" not in references
        if method.name == "fit":
            assert "RepresentationFrame" not in references
            assert _mentions_type(method.returns, aliases, "FittedPayload")
            assert not _mentions_type(method.returns, aliases, "FittedState")

    protocol_references = {
        name: {
            _reference_leaf(reference)
            for reference in _expression_references(_only_class(tree, name), aliases)
        }
        for name in PHASE3_PROTOCOL_SURFACES
    }
    assert "ModelInputs" in protocol_references["ResearchModel"]
    assert "ModelInputs" in protocol_references["CorpusResearchModel"]
    assert "CorpusModelInputs" in protocol_references["CorpusResearchModel"]
    assert all(
        "ModelInputs" not in references
        for name, references in protocol_references.items()
        if name not in {"CorpusResearchModel", "ResearchModel"}
    )


def test_slice_3a_contracts_leak_no_data_handle_or_table_library() -> None:
    """Core research signatures expose frames and descriptors, never loaders or tables."""
    forbidden = {
        "AccessGrant",
        "BoundedView",
        "FeatureRequest",
        "MarketFrame",
        *FORBIDDEN_PUBLIC_TYPE_LIBRARIES,
    }
    violations: list[str] = []
    for relative in ("core/research.py", "core/research_frames.py", "core/interfaces.py"):
        tree = ast.parse((PACKAGE_ROOT / relative).read_text())
        aliases = _symbol_aliases(tree)
        for site, annotation in _public_annotation_sites(tree):
            if relative == "core/interfaces.py" and site.startswith("FeatureProvider."):
                continue
            leaves = {
                _reference_leaf(reference)
                for reference in _expression_references(annotation, aliases)
            }
            overlap = leaves & forbidden
            if overlap:
                violations.append(f"{relative} {site} exposes {sorted(overlap)}")
    assert not violations, "Phase-3 public contract leakage: " + "; ".join(violations)


def test_slice_4b_module_exports_exactly_the_closed_public_vocabulary() -> None:
    """Slice 4B adds only the accepted execution values and private carriers."""
    path = PACKAGE_ROOT / "core" / "simulation.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    expected = {*PHASE4_SIMULATION_EXPORTS, "annotations"}

    assert _declared_exports(tree) == PHASE4_SIMULATION_EXPORTS
    assert _module_level_public_names(tree) == expected
    module = importlib.import_module(f"{ROOT_NAME}.core.simulation")
    assert set(_runtime_public_bindings(module)) == expected


def test_slice_4b_value_and_carrier_surfaces_are_exact() -> None:
    """No generic artifact base, extra knob, or unplanned public method exists."""
    tree = ast.parse((PACKAGE_ROOT / "core" / "simulation.py").read_text())
    expected_values = {
        "ExecutionCostBinding": {
            "account_currency_per_quote_currency",
            "binding_digest",
            "binding_id",
            "commission_basis",
            "commission_per_lot",
            "contract_size",
            "cost_semantics",
            "execution_price_model",
            "price_units_per_pip",
            "spread_pips",
        },
        "ExecutionSpec": {
            "cost_binding",
            "execution_id",
            "lot_size",
            "stop_loss_pips",
            "take_profit_pips",
            "version",
        },
        "SimulationRequest": PHASE4_SIMULATION_REQUEST_SURFACE,
        "TradeLog": PHASE4_TRADE_LOG_SURFACE,
        "EquityCurve": PHASE4_EQUITY_CURVE_SURFACE,
    }
    for name, surface in expected_values.items():
        node = _only_class(tree, name)
        assert not node.bases, f"{name} must not acquire a generic artifact base"
        assert _class_public_surface(node) == surface


def test_slice_4b_enums_are_exact_direct_strenum_vocabularies() -> None:
    """The three evidence enums and exit-reason vocabulary cannot grow silently."""
    tree = ast.parse((PACKAGE_ROOT / "core" / "simulation.py").read_text())
    expected = {
        "CommissionBasis": {
            "PER_ROUND_TURN": "per_round_turn",
            "PER_SIDE": "per_side",
        },
        "CostSemanticsStatus": {
            "UNVERIFIED": "unverified",
            "VERIFIED": "verified",
        },
        "ExecutionPriceModel": {
            "RECORDED_PATH_UNVERIFIED": "recorded_path_unverified",
            "SYNTHETIC_DECLARED": "synthetic_declared",
        },
        "TradeExitReason": {
            "BOUNDARY_FORCED_CLOSE": "boundary_forced_close",
            "STOP_LOSS": "stop_loss",
            "TAKE_PROFIT": "take_profit",
            "TARGET_CHANGE": "target_change",
        },
    }
    for name, members in expected.items():
        node = _only_class(tree, name)
        assert {
            _reference_leaf(reference)
            for reference in _expression_references(node.bases[0], _symbol_aliases(tree))
        } == {"StrEnum"}
        observed = {
            target.id: assignment.value.value
            for assignment in node.body
            if isinstance(assignment, ast.Assign)
            and len(assignment.targets) == 1
            and isinstance((target := assignment.targets[0]), ast.Name)
            and isinstance(assignment.value, ast.Constant)
            and isinstance(assignment.value.value, str)
        }
        assert observed == members


def test_slice_4b_verified_binding_authority_is_pinned_to_none() -> None:
    """Opening the audit cannot be bypassed by a plausible source-tree default."""
    path = PACKAGE_ROOT / "core" / "simulation.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    bindings = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign | ast.AnnAssign)
        and "VERIFIED_COST_BINDING" in _assigned_names(node)
    ]
    assert len(bindings) == 1
    binding = bindings[0]
    assert isinstance(binding, ast.AnnAssign)
    assert isinstance(binding.value, ast.Constant)
    assert binding.value.value is None
    annotation_references = {
        _reference_leaf(reference)
        for reference in _expression_references(binding.annotation, _symbol_aliases(tree))
    }
    assert {"ExecutionCostBinding", "Final"} <= annotation_references

    module = importlib.import_module(f"{ROOT_NAME}.core.simulation")
    assert module.VERIFIED_COST_BINDING is None


def test_slice_4b_contract_path_has_no_io_data_civil_time_or_table_library() -> None:
    """Contracts cannot load rows, resolve config, persist artifacts, or infer time."""
    path = PACKAGE_ROOT / "core" / "simulation.py"
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    aliases = _symbol_aliases(tree)
    forbidden_imports = {
        "calendar",
        "dateutil",
        "duckdb",
        "os",
        "pandas",
        "pathlib",
        "polars",
        "pyarrow",
        "pytz",
        "sqlite3",
        "subprocess",
        "time",
        "zoneinfo",
    }
    forbidden_calls = {
        "fromfile",
        "getenv",
        "glob",
        "load",
        "memmap",
        "open",
        "read_bytes",
        "read_text",
        "save",
        "savez",
        "tofile",
        "write_bytes",
        "write_text",
    }
    violations = [
        f"imports {name}" for name in sorted(_imported_module_roots(tree) & forbidden_imports)
    ]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called = {
                _reference_leaf(reference)
                for reference in _expression_references(node.func, aliases)
            }
            violations.extend(f"calls {name}" for name in sorted(called & forbidden_calls))
        identifier: str | None = None
        if isinstance(node, ast.Name):
            identifier = node.id
        elif isinstance(node, ast.Attribute):
            identifier = node.attr
        elif isinstance(node, ast.arg):
            identifier = node.arg
        elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            identifier = node.name
        if identifier is not None and "utc" in identifier.lower().strip("_").split("_"):
            violations.append(f"declares UTC identifier {identifier!r}")
    assert f"{ROOT_NAME}.data" not in _absolute_import_modules(tree)
    assert not violations, "Slice 4B boundary violations: " + "; ".join(violations)


def test_slice_4b_public_signatures_use_only_numpy_columnar_contracts() -> None:
    """No table library or data authority crosses the Phase-4 public surface."""
    forbidden = {
        "AccessGrant",
        "BoundedView",
        "GapIndex",
        "MarketFrame",
        *FORBIDDEN_PUBLIC_TYPE_LIBRARIES,
    }
    violations: list[str] = []
    for relative in ("core/simulation.py", "core/interfaces.py"):
        tree = ast.parse((PACKAGE_ROOT / relative).read_text())
        aliases = _symbol_aliases(tree)
        for site, annotation in _public_annotation_sites(tree):
            if relative == "core/interfaces.py" and not site.startswith("TradeEvaluator."):
                continue
            leaves = {
                _reference_leaf(reference)
                for reference in _expression_references(annotation, aliases)
            }
            if overlap := leaves & forbidden:
                violations.append(f"{relative} {site} exposes {sorted(overlap)}")
    assert not violations, "Phase-4 public contract leakage: " + "; ".join(violations)


def test_slice_4c_simulator_surface_and_deferred_vocabulary_are_exact() -> None:
    """The one pure simulator exists without a speculative execution framework."""
    paths = (
        PACKAGE_ROOT / "core" / "simulation.py",
        PACKAGE_ROOT / "simulation" / "__init__.py",
        PACKAGE_ROOT / "simulation" / "bracket.py",
    )
    source = "\n".join(path.read_text().lower() for path in paths)
    forbidden = {
        "accountstate",
        "fillrequest",
        "gapindex",
        "margin",
        "orderbook",
        "orderrequest",
        "partialfill",
        "portfoliopolicy",
        "positionlimits",
        "sharpe",
        "slippagemodel",
        "trailingstop",
    }
    assert not {term for term in forbidden if term in source}

    package_tree = ast.parse(paths[1].read_text(), filename=str(paths[1]))
    engine_tree = ast.parse(paths[2].read_text(), filename=str(paths[2]))
    expected = {
        "SUPPORTED_INTENT_SCHEMA_IDS",
        "simulate_bracket_non_interruptible_v1",
        "simulate_bracket_single_position_v1",
    }
    assert _declared_exports(package_tree) == expected
    assert _module_level_public_names(package_tree) == expected
    assert _declared_exports(engine_tree) == expected
    assert _module_level_public_names(engine_tree) == {*expected, "annotations"}

    simulator = importlib.import_module(f"{ROOT_NAME}.simulation")
    assert set(_runtime_public_bindings(simulator)) == expected
    assert simulator.SUPPORTED_INTENT_SCHEMA_IDS == (
        "target_exposure_intent_v1",
        "bracketed_exposure_intent_v1",
    )


def test_slice_4c_simulator_imports_only_core_and_has_no_side_effect_authority() -> None:
    """The engine is pure and cannot load data, persist, log, or read a clock."""
    path = PACKAGE_ROOT / "simulation" / "bracket.py"
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    internal = {
        module for module in _absolute_import_modules(tree) if module.startswith(f"{ROOT_NAME}.")
    }
    assert internal
    assert all(module.startswith(f"{ROOT_NAME}.core") for module in internal)
    forbidden_imports = {
        "datetime",
        "logging",
        "os",
        "pathlib",
        "random",
        "secrets",
        "time",
        f"{ROOT_NAME}.artifacts",
        f"{ROOT_NAME}.data",
        f"{ROOT_NAME}.experiments",
    }
    imported = _absolute_import_modules(tree)
    assert not {
        forbidden
        for forbidden in forbidden_imports
        if any(module == forbidden or module.startswith(f"{forbidden}.") for module in imported)
    }
    calls = {_qualified_name(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert not {"open", "print", "input"}.intersection(calls)


def test_slice_4d_has_exactly_ten_test_only_adversarial_controls() -> None:
    """The finite control inventory and both timing rigs remain release-blocking.

    Two controls were added with per-trade brackets: one that lets a bracket
    carry into a later trade, and one that lets a held-position row rebind an
    open trade's stop. Both are the failure modes the new capability introduces.
    """
    tree = ast.parse(
        PHASE4_ADVERSARIAL_TEST_PATH.read_text(),
        filename=str(PHASE4_ADVERSARIAL_TEST_PATH),
    )
    observed = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name.endswith("Control")
        and not node.name.startswith("_")
    }
    assert observed == PHASE4_ADVERSARIAL_CONTROL_CLASSES
    assert not _slice_2c_gate_evasion_violations(tree)
    tests = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }

    def _control_prefix(name: str) -> str:
        """Reduce test_ac<N>_<rest> to test_ac<N>_ for any width of <N>."""
        if not name.startswith("test_ac"):
            return name
        digits = name[len("test_ac") :]
        index = len(digits) - len(digits.lstrip("0123456789"))
        return name[: len("test_ac") + index + 1]

    assert {
        "test_live_simulator_passes_both_exact_timing_rigs",
        "test_control_inventory_and_rig_coverage_are_exact_and_non_vacuous",
        *(f"test_ac{index}_" for index in range(1, 11)),
    } <= {_control_prefix(name) for name in tests} | set(tests)
    references = {
        _reference_leaf(reference)
        for reference in _expression_references(tree, _symbol_aliases(tree))
    }
    assert {"_assert_future_intent_safe", "_assert_future_price_safe"} <= references


def test_slice_4d_controls_and_slice_4g_canaries_never_reach_production() -> None:
    """Mutation controls and cheating/random canaries cannot enter a live factory."""
    forbidden = {*PHASE4_ADVERSARIAL_CONTROL_CLASSES, *PHASE4_CANARY_IDS}
    violations: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        references = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        references.update(node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute))
        references.update(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        )
        string_values = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        if overlap := forbidden & (references | string_values):
            violations.append(f"{path.relative_to(PACKAGE_ROOT)} contains {sorted(overlap)}")
    assert not violations, "Phase-4 test harness escaped into production: " + "; ".join(violations)


def test_slice_4g_canary_inventory_is_exact_and_always_active() -> None:
    """All four seeded inversion canaries stay finite and non-skippable."""
    tree = ast.parse(
        PHASE4_CANARY_TEST_PATH.read_text(),
        filename=str(PHASE4_CANARY_TEST_PATH),
    )
    assert not _slice_2c_gate_evasion_violations(tree)
    assignments = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    canary_ids = assignments["CANARY_IDS"]
    assert isinstance(canary_ids, ast.Call)
    assert set(ast.literal_eval(canary_ids.args[0])) == PHASE4_CANARY_IDS
    assert ast.literal_eval(assignments["_K2_MAX_GROSS_DEVIATION_FRACTION_OF_COST"]) == 0.02
    tests = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_k")
    }
    assert {"test_k1_", "test_k2_", "test_k3_", "test_k4_"} == {name[:8] for name in tests}
    k2_references = {
        _reference_leaf(reference)
        for reference in _expression_references(
            tests["test_k2_seeded_random_policy_loses_within_relative_cost_band"],
            _symbol_aliases(tree),
        )
    }
    assert {
        "_K2_MAX_GROSS_DEVIATION_FRACTION_OF_COST",
        "abs",
        "net_pnl",
        "total_cost",
    } <= k2_references


def test_slice_4h_performance_harness_is_full_train_only_and_release_blocking() -> None:
    """Qualification pins the full dense axis, budget method and zero audit delta."""
    source = PHASE4_PERFORMANCE_TEST_PATH.read_text()
    tree = ast.parse(source, filename=str(PHASE4_PERFORMANCE_TEST_PATH))
    assignments = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assignments["_TIMED_REPETITIONS"]) == 5
    assert ast.literal_eval(assignments["_BUDGET_SECONDS"]) == 60.0
    assert ast.literal_eval(assignments["_EXPECTED_AUDIT_RECORDS"]) == 3
    assert ast.literal_eval(assignments["_EXPECTED_AUDIT_SHA256"]) == (
        "deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5"
    )
    tests = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }
    qualification = tests["test_published_train_full_axis_simulation_meets_budget"]
    references = {
        _reference_leaf(reference)
        for reference in _expression_references(qualification, _symbol_aliases(tree))
    }
    assert {
        "_audit_snapshot",
        "_clean_committed_head",
        "_peak_rss_bytes",
        "_run_once",
        "_summarize",
        "TRAIN_EXPECTED_ROWS",
    } <= references
    run_once = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_once"
    )
    run_once_references = {
        _reference_leaf(reference)
        for reference in _expression_references(run_once, _symbol_aliases(tree))
    }
    assert "_load_train_ohlc" in run_once_references
    load_line = next(
        node.lineno
        for node in ast.walk(run_once)
        if isinstance(node, ast.Call) and _qualified_name(node.func) == "_load_train_ohlc"
    )
    timer_line = next(
        node.lineno
        for node in ast.walk(run_once)
        if isinstance(node, ast.Call) and _qualified_name(node.func) == "perf_counter"
    )
    assert timer_line < load_line
    assert source.count("_run_once(spec)") == 2
    assert 'cost_semantics": "unverified' in source
    assert 'execution_price_model": "recorded_path_unverified' in source
    assert not any(
        identity in source
        for identity in (
            "XAUUSD_S_M1_LOCKBOX",
            "XAUUSD_S_M1_OOS",
            "XAUUSD_S_M1_SNAPSHOT",
            "XAUUSD_S_M1_MT5_V1",
            "XAUUSD_S_M1_CURATED_V1",
        )
    )
    calls = {_qualified_name(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert not {"pytest.skip", "pytest.xfail", "pytest.approx"}.intersection(calls)


def test_slice_4f_config_resolution_is_narrow_and_synthetic_model_is_unreachable() -> None:
    """Execution vocabulary reaches only the accepted composition-root surfaces."""
    accepted = {
        "config.py",
        "evaluation.py",
        "orchestration.py",
        "training_bundles.py",
        "uls_entry_bundles.py",
        "uls_joint_bundles.py",
    }
    violations: list[str] = []
    for path in _layer_files("experiments"):
        lowered = path.read_text().lower()
        relative = path.relative_to(PACKAGE_ROOT / "experiments").as_posix()
        if "synthetic_declared" in lowered:
            violations.append(f"{relative} resolves the test-only synthetic model")
        if relative not in accepted:
            violations.extend(
                f"{relative} contains execution contract {term}"
                for term in (
                    "executioncostbinding",
                    "executionpricemodel",
                    "executionspec",
                    "tradelog",
                    "equitycurve",
                )
                if term in lowered
            )
    assert not violations, "Slice 4F config scope violations: " + "; ".join(violations)


def test_slice_3b_outcome_layer_has_one_exact_public_component() -> None:
    """V1 ships one OutcomeSpec and no speculative outcome library."""
    package_tree = ast.parse((PACKAGE_ROOT / "outcomes" / "__init__.py").read_text())
    registry_tree = ast.parse((PACKAGE_ROOT / "outcomes" / "registry.py").read_text())
    reference_tree = ast.parse((PACKAGE_ROOT / "outcomes" / "reference.py").read_text())
    execution_tree = ast.parse((PACKAGE_ROOT / "outcomes" / "execution.py").read_text())

    assert _declared_exports(package_tree) == {"OUTCOME_SPECS"}
    assert _module_level_public_names(package_tree) == {"OUTCOME_SPECS"}
    assert _declared_exports(registry_tree) == {"OUTCOME_SPECS"}
    assert _declared_exports(reference_tree) == {"ForwardLogReturn"}
    assert _module_level_public_names(reference_tree) == {
        "ForwardLogReturn",
        "annotations",
    }
    assert not _declared_exports(execution_tree)
    assert _module_level_public_names(execution_tree) == {"annotations"}
    assert (
        _class_public_surface(_only_class(reference_tree, "ForwardLogReturn"))
        == SLICE_3B_OUTCOME_SURFACE
    )

    outcomes = importlib.import_module(f"{ROOT_NAME}.outcomes")
    assert outcomes.OUTCOME_SPECS.names() == frozenset({"forward_log_return_v1"})


def test_slice_3b_constructor_has_no_hidden_outcome_default() -> None:
    """Every research-definition choice is an explicit keyword argument."""
    tree = ast.parse((PACKAGE_ROOT / "outcomes" / "reference.py").read_text())
    component = _only_class(tree, "ForwardLogReturn")
    constructor = next(
        node
        for node in component.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    assert [argument.arg for argument in constructor.args.args] == ["self"]
    assert [argument.arg for argument in constructor.args.kwonlyargs] == [
        "horizon_rows",
        "decision_delay_rows",
        "reference_price_column",
        "boundary_policy",
    ]
    assert constructor.args.kw_defaults == [None, None, None, None]


def test_slice_3b_forward_reading_is_confined_to_outcomes() -> None:
    """OutcomeRequest never reaches features, models, representations, or policies."""
    permitted = {
        "core/__init__.py",
        "core/interfaces.py",
        "core/research.py",
        "outcomes/execution.py",
        "outcomes/reference.py",
        "experiments/outcomes.py",
    }
    violations: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative = path.relative_to(PACKAGE_ROOT).as_posix()
        if relative in permitted:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        if _forbidden_identifier_violations(tree, {"OutcomeRequest"}):
            violations.append(relative)
    assert not violations, "OutcomeRequest escaped its fixed stage: " + ", ".join(violations)


def test_slice_3b_outcome_source_has_no_feature_gap_or_session_semantics() -> None:
    """Observed-row outcomes neither share GapPolicy nor interpret clock discontinuities."""
    forbidden_identifiers = {
        "CalendarTimePolicy",
        "GapIndex",
        "GapPolicy",
        "datetime",
        "timedelta",
        "timezone",
    }
    forbidden_words = {"closure", "holiday", "outage", "session", "weekend"}
    violations: list[str] = []
    for path in _layer_files("outcomes"):
        tree = ast.parse(path.read_text(), filename=str(path))
        references = {
            _reference_leaf(reference)
            for reference in _expression_references(tree, _symbol_aliases(tree))
        }
        overlap = references & forbidden_identifiers
        if overlap:
            violations.append(f"{path.name} references {sorted(overlap)}")
        lowered = path.read_text().lower()
        words = {word for word in forbidden_words if word in lowered}
        if words:
            violations.append(f"{path.name} names semantic gaps {sorted(words)}")
    assert not violations, "Outcome semantic-boundary violations: " + "; ".join(violations)


def test_slice_3b_leakage_rigs_name_all_controls_and_cannot_be_softened() -> None:
    """Every approved negative control is test-only and exact comparison stays mandatory."""
    path = Path(__file__).with_name("test_outcome_leakage.py")
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    observed = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name in SLICE_3B_NEGATIVE_CONTROL_CLASSES
    }
    assert observed == SLICE_3B_NEGATIVE_CONTROL_CLASSES
    assert not any(
        isinstance(node, ast.FunctionDef)
        and any(
            isinstance(decorator, ast.Attribute) and decorator.attr in {"skip", "skipif", "xfail"}
            for decorator in node.decorator_list
        )
        for node in ast.walk(tree)
    )
    lowered = source.lower()
    assert "allclose" not in lowered
    assert "tolerance" not in lowered

    production_controls: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        production_tree = ast.parse(path.read_text(), filename=str(path))
        production_controls.extend(
            node.name
            for node in ast.walk(production_tree)
            if isinstance(node, ast.ClassDef) and node.name in SLICE_3B_NEGATIVE_CONTROL_CLASSES
        )
    assert not production_controls


def test_slice_3b_experiment_entry_points_only_mint_the_two_target_channels() -> None:
    """Outcome computation has two explicit carrier destinations and no storage authority."""
    path = PACKAGE_ROOT / "experiments" / "outcomes.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    assert _declared_exports(tree) == {"compute_outcomes", "compute_training_targets"}
    assert _module_level_public_names(tree) == {
        "annotations",
        "compute_outcomes",
        "compute_training_targets",
    }
    imports = _imported_module_roots(tree)
    assert not (
        imports
        & {
            "fcntl",
            "os",
            "pathlib",
            "trading_research.artifacts",
            "trading_research.data",
        }
    )


def test_slice_3c_module_inventory_and_public_surface_are_exact() -> None:
    """3C adds the fixed lifecycle implementation without a new public type."""
    for layer, expected in SLICE_3C_LAYER_INVENTORIES.items():
        root = PACKAGE_ROOT / layer
        observed = {path.relative_to(root).as_posix() for path in root.rglob("*.py")}
        assert observed == expected

    private_modules = (
        "artifacts/fitted.py",
        "models/execution.py",
        "representations/execution.py",
    )
    for relative in private_modules:
        tree = ast.parse((PACKAGE_ROOT / relative).read_text())
        assert not _declared_exports(tree)
        assert _module_level_public_names(tree) == {"annotations"}

    fitting_tree = ast.parse((PACKAGE_ROOT / "experiments" / "fitting.py").read_text())
    assert _declared_exports(fitting_tree) == SLICE_3C_FITTING_EXPORTS
    assert _module_level_public_names(fitting_tree) == {
        *SLICE_3C_FITTING_EXPORTS,
        "annotations",
    }
    assert not any(isinstance(node, ast.ClassDef) for node in fitting_tree.body)


def test_slice_3c_reference_components_and_registry_provenance_are_exact() -> None:
    """Exactly four models and one representation prove the approved lifecycle."""
    model_tree = ast.parse((PACKAGE_ROOT / "models" / "reference.py").read_text())
    representation_tree = ast.parse((PACKAGE_ROOT / "representations" / "reference.py").read_text())
    model_classes = set(SLICE_3C_MODEL_CLASSES.values())
    representation_classes = set(SLICE_3C_REPRESENTATION_CLASSES.values())
    assert _declared_exports(model_tree) == model_classes
    assert _module_level_public_names(model_tree) == {*model_classes, "annotations"}
    assert _declared_exports(representation_tree) == representation_classes
    assert _module_level_public_names(representation_tree) == {
        *representation_classes,
        "annotations",
    }
    for name in model_classes:
        component = _only_class(model_tree, name)
        assert _class_public_surface(component) == PHASE3_PROTOCOL_SURFACES["ResearchModel"]
        assert not any(
            isinstance(member, ast.FunctionDef) and member.name == "__init__"
            for member in component.body
        )
    for name in representation_classes:
        component = _only_class(representation_tree, name)
        assert _class_public_surface(component) == PHASE3_PROTOCOL_SURFACES["RepresentationModel"]
        assert not any(
            isinstance(member, ast.FunctionDef) and member.name == "__init__"
            for member in component.body
        )

    models = importlib.import_module(f"{ROOT_NAME}.models").RESEARCH_MODELS
    representations = importlib.import_module(f"{ROOT_NAME}.representations").REPRESENTATION_MODELS
    assert models.names() == frozenset(SLICE_3C_MODEL_CLASSES)
    assert representations.names() == frozenset(SLICE_3C_REPRESENTATION_CLASSES)
    for registry_name, class_name in SLICE_3C_MODEL_CLASSES.items():
        component_type = type(models.create(registry_name))
        assert (component_type.__module__, component_type.__name__) == (
            f"{ROOT_NAME}.models.reference",
            class_name,
        )
    for registry_name, class_name in SLICE_3C_REPRESENTATION_CLASSES.items():
        component_type = type(representations.create(registry_name))
        assert (component_type.__module__, component_type.__name__) == (
            f"{ROOT_NAME}.representations.reference",
            class_name,
        )


def test_slice_3c_plugins_return_payloads_but_have_no_state_identity_authority() -> None:
    """Plugins may learn content but only the composition root can bind identity."""
    forbidden_authority = {
        "ArtifactStore",
        "MaterializationPolicy",
        "PlatformIdentity",
        "SplitPolicyIdentity",
        "StageIdentity",
        "_FittedStateIdentity",
        "_bind_fitted_state",
        "_make_fitted_state",
        "dependency_lock_sha256",
        "fit_isolation",
    }
    violations: list[str] = []
    for layer in ("models", "representations"):
        for path in _layer_files(layer):
            tree = ast.parse(path.read_text(), filename=str(path))
            references = {
                _reference_leaf(reference)
                for reference in _expression_references(tree, _symbol_aliases(tree))
            }
            overlap = references & forbidden_authority
            if overlap:
                violations.append(f"{path.relative_to(PACKAGE_ROOT)} names {sorted(overlap)}")
    assert not violations, "plugin identity-authority leakage: " + "; ".join(violations)

    for relative, class_names in (
        ("models/reference.py", set(SLICE_3C_MODEL_CLASSES.values())),
        (
            "representations/reference.py",
            set(SLICE_3C_REPRESENTATION_CLASSES.values()),
        ),
    ):
        tree = ast.parse((PACKAGE_ROOT / relative).read_text())
        aliases = _symbol_aliases(tree)
        for class_name in class_names:
            component = _only_class(tree, class_name)
            fit = next(
                member
                for member in component.body
                if isinstance(member, ast.FunctionDef) and member.name == "fit"
            )
            assert _mentions_type(fit.returns, aliases, "FittedPayload")
            assert not _mentions_type(fit.returns, aliases, "FittedState")


def test_slice_3c_representation_chaining_remains_structurally_unavailable() -> None:
    """Concrete representation code has no ModelInputs argument or dependency."""
    for path in _layer_files("representations"):
        tree = ast.parse(path.read_text(), filename=str(path))
        references = {
            _reference_leaf(reference)
            for reference in _expression_references(tree, _symbol_aliases(tree))
        }
        assert "ModelInputs" not in references, path.relative_to(PACKAGE_ROOT)

    tree = ast.parse((PACKAGE_ROOT / "representations" / "reference.py").read_text())
    aliases = _symbol_aliases(tree)
    component = _only_class(tree, "TrailingStandardizer")
    for method_name in ("fit", "encode"):
        method = next(
            member
            for member in component.body
            if isinstance(member, ast.FunctionDef) and member.name == method_name
        )
        assert _mentions_type(method.args.args[-1].annotation, aliases, "FeatureFrame")
        assert not any(
            _mentions_type(annotation, aliases, "RepresentationFrame")
            for _, annotation in _function_annotation_sites(method_name, method)
            if method_name == "fit"
        )


def test_slice_3c_fitted_identity_serialization_and_store_namespace_are_pinned() -> None:
    """The full identity is hashed, and fitted bytes cannot activate pickle."""
    fitted_path = PACKAGE_ROOT / "artifacts" / "fitted.py"
    tree = ast.parse(fitted_path.read_text(), filename=str(fitted_path))
    fitted = importlib.import_module(f"{ROOT_NAME}.artifacts.fitted")
    store = importlib.import_module(f"{ROOT_NAME}.artifacts.store")
    assert frozenset(SLICE_3C_FITTED_IDENTITY_FIELDS) == fitted._IDENTITY_FIELDS
    assert frozenset({"fitted-states"}) == store._PRIVATE_NAMESPACES

    imports = _absolute_import_modules(tree)
    assert "pickle" not in imports
    load_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and "load"
        in {
            _reference_leaf(reference)
            for reference in _expression_references(node.func, _symbol_aliases(tree))
        }
    ]
    assert len(load_calls) == 1
    allow_pickle = [
        keyword.value for keyword in load_calls[0].keywords if keyword.arg == "allow_pickle"
    ]
    assert len(allow_pickle) == 1
    assert isinstance(allow_pickle[0], ast.Constant)
    assert allow_pickle[0].value is False

    layer_sources = "\n".join(
        path.read_text() for layer in ("models", "representations") for path in _layer_files(layer)
    )
    assert "_make_fitted_state" not in layer_sources
    assert "_FittedStateIdentity" not in layer_sources


def test_slice_3d_live_registry_coverage_and_negative_controls_are_exact() -> None:
    """Adding a live component without rig support must fail the architecture gate."""
    source = SLICE_3D_LEAKAGE_TEST_PATH.read_text()
    tree = ast.parse(source, filename=str(SLICE_3D_LEAKAGE_TEST_PATH))
    observed_controls = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in SLICE_3D_NEGATIVE_CONTROL_CLASSES
    }
    assert observed_controls == SLICE_3D_NEGATIVE_CONTROL_CLASSES

    def _assigned_value(name: str) -> ast.AST:
        matches = [
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign | ast.AnnAssign)
            and name in _assigned_names(node)
            and node.value is not None
        ]
        assert len(matches) == 1
        return matches[0]

    expected_live_models = ast.parse(
        "tuple(sorted(RESEARCH_MODELS.names()))",
        mode="eval",
    ).body
    expected_live_representations = ast.parse(
        "tuple(sorted(REPRESENTATION_MODELS.names()))",
        mode="eval",
    ).body
    assert ast.dump(_assigned_value("LIVE_MODEL_NAMES")) == ast.dump(expected_live_models)
    assert ast.dump(_assigned_value("LIVE_REPRESENTATION_NAMES")) == ast.dump(
        expected_live_representations
    )
    covered_models = {
        node.value
        for node in ast.walk(_assigned_value("COVERED_MODEL_NAMES"))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    covered_representations = {
        node.value
        for node in ast.walk(_assigned_value("COVERED_REPRESENTATION_NAMES"))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert covered_models == SLICE_3D_COVERED_MODELS
    assert covered_representations == SLICE_3D_COVERED_REPRESENTATIONS

    parametrized: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "parametrize"
                and len(decorator.args) >= 2
                and isinstance(decorator.args[1], ast.Name)
            ):
                parametrized.setdefault(node.name, set()).add(decorator.args[1].id)
    assert parametrized["test_every_registered_model_is_forward_blind"] == {"LIVE_MODEL_NAMES"}
    assert parametrized["test_every_registered_model_is_row_local"] == {"LIVE_MODEL_NAMES"}
    assert parametrized["test_every_registered_representation_is_forward_blind"] == {
        "LIVE_REPRESENTATION_NAMES"
    }
    assert parametrized["test_every_registered_representation_is_row_local"] == {
        "LIVE_REPRESENTATION_NAMES"
    }

    models = importlib.import_module(f"{ROOT_NAME}.models").RESEARCH_MODELS
    representations = importlib.import_module(f"{ROOT_NAME}.representations").REPRESENTATION_MODELS
    assert models.names() == frozenset(SLICE_3D_COVERED_MODELS)
    assert representations.names() == frozenset(SLICE_3D_COVERED_REPRESENTATIONS)

    production_controls: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        production_tree = ast.parse(path.read_text(), filename=str(path))
        production_controls.extend(
            node.name
            for node in ast.walk(production_tree)
            if isinstance(node, ast.ClassDef) and node.name in SLICE_3D_NEGATIVE_CONTROL_CLASSES
        )
    assert not production_controls


def test_slice_3d_rigs_require_exact_bits_finite_witnesses_and_no_escape() -> None:
    """The leakage gate cannot be softened by tolerance, skipping, or vacuity."""
    source = SLICE_3D_LEAKAGE_TEST_PATH.read_text()
    tree = ast.parse(source, filename=str(SLICE_3D_LEAKAGE_TEST_PATH))
    assert not _slice_2c_gate_evasion_violations(tree)
    assert "ToleranceSpec" not in source

    bit_helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_assert_array_bits_equal"
    )
    comparisons = {ast.dump(node) for node in ast.walk(bit_helper) if isinstance(node, ast.Compare)}
    required_comparisons = (
        "np.isnan(left).tobytes() != np.isnan(right).tobytes()",
        "left.view(np.uint64).tobytes() != right.view(np.uint64).tobytes()",
    )
    for expression in required_comparisons:
        assert ast.dump(ast.parse(expression, mode="eval").body) in comparisons

    frame_helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_assert_frames_exact"
    )
    frame_expressions = {ast.dump(node) for node in ast.walk(frame_helper)}
    finite_witness = ast.parse(
        "not np.isfinite(left_values).any()",
        mode="eval",
    ).body
    assert ast.dump(finite_witness) in frame_expressions
    exact_calls = [
        node
        for node in ast.walk(frame_helper)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_assert_array_bits_equal"
    ]
    assert len(exact_calls) == 1
    assert [argument.id for argument in exact_calls[0].args if isinstance(argument, ast.Name)] == [
        "left_values",
        "right_values",
    ]

    forward = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_assert_forward_safe"
    )
    locality = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_assert_row_local"
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_assert_frames_exact"
        for node in ast.walk(forward)
    )
    assert any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "perturbed_values"
            for target in node.targets
        )
        for node in ast.walk(locality)
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_assert_frames_exact"
        for node in ast.walk(locality)
    )


def test_slice_3d_production_inference_has_no_forward_or_carried_state_authority() -> None:
    """Registered layers can only compute from core inputs and fixed state."""
    forbidden_import_roots = {
        "datetime",
        "http",
        "os",
        "pandas",
        "pathlib",
        "polars",
        "random",
        "requests",
        "socket",
        "subprocess",
        "time",
        "urllib",
    }
    forbidden_references = {
        "BoundedView",
        "FeatureRequest",
        "MarketFrame",
        "OutcomeRequest",
        "__import__",
        "fromfile",
        "getenv",
        "glob",
        "load",
        "max_prior_rows",
        "memmap",
        "open",
        "read_bytes",
        "read_text",
    }
    violations: list[str] = []
    for layer in ("models", "representations"):
        for path in _layer_files(layer):
            tree = ast.parse(path.read_text(), filename=str(path))
            aliases = _symbol_aliases(tree)
            roots = {module.split(".", maxsplit=1)[0] for module in _absolute_import_modules(tree)}
            overlap = roots & forbidden_import_roots
            if overlap:
                violations.append(f"{path.relative_to(PACKAGE_ROOT)} imports {sorted(overlap)}")
            references = {
                _reference_leaf(reference) for reference in _expression_references(tree, aliases)
            }
            escaped = references & forbidden_references
            if escaped:
                violations.append(f"{path.relative_to(PACKAGE_ROOT)} references {sorted(escaped)}")
            for component in (node for node in tree.body if isinstance(node, ast.ClassDef)):
                for method in component.body:
                    if not isinstance(method, ast.FunctionDef) or method.name not in {
                        "encode",
                        "infer",
                    }:
                        continue
                    for assignment in (
                        node
                        for node in ast.walk(method)
                        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign)
                    ):
                        targets = (
                            assignment.targets
                            if isinstance(assignment, ast.Assign)
                            else [assignment.target]
                        )
                        if any(
                            isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "self"
                            for target in targets
                        ):
                            violations.append(
                                f"{path.relative_to(PACKAGE_ROOT)} mutates self during "
                                f"{component.name}.{method.name}"
                            )
    assert not violations, "Slice 3D production-authority violations: " + "; ".join(violations)


def test_slice_3e_fold_module_is_private_fixed_and_has_no_hidden_defaults() -> None:
    """3E adds one private fixed-length generator and no fold framework."""
    experiments_root = PACKAGE_ROOT / "experiments"
    inventory = {
        path.relative_to(experiments_root).as_posix() for path in experiments_root.rglob("*.py")
    }
    assert inventory == PHASE3_EXPERIMENT_INVENTORY

    fold_path = experiments_root / "folds.py"
    fold_source = fold_path.read_text()
    fold_tree = ast.parse(fold_source, filename=str(fold_path))
    assert not _declared_exports(fold_tree)
    assert _module_level_public_names(fold_tree) == {"annotations"}
    assert "fit_mode" not in fold_source
    assert "expanding" not in fold_source.lower()
    assert "Registry" not in fold_source

    generator = next(
        node
        for node in fold_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_generate_walk_forward_folds"
    )
    assert not generator.args.args
    assert [argument.arg for argument in generator.args.kwonlyargs] == [
        "view",
        "features",
        "outcome_specs",
        "fit_rows",
        "eval_rows",
        "step_rows",
        "embargo_rows",
        "purge_override",
    ]
    assert generator.args.kw_defaults == [None] * 8
    assert generator.args.vararg is None
    assert generator.args.kwarg is None

    fitting_path = experiments_root / "fitting.py"
    fitting_tree = ast.parse(fitting_path.read_text(), filename=str(fitting_path))
    aliases = _symbol_aliases(fitting_tree)
    for function_name in ("fit_model", "fit_representation"):
        function = next(
            node
            for node in fitting_tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        isolation = next(
            argument for argument in function.args.kwonlyargs if argument.arg == "fit_isolation"
        )
        assert _mentions_type(isolation.annotation, aliases, "_FitIsolation")
        function_references = {
            _reference_leaf(reference) for reference in _expression_references(function, aliases)
        }
        assert {"_identity_params", "_validate_fit"} <= function_references

    constructors: list[str] = []
    for path in sorted(experiments_root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        aliases = _symbol_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            leaves = {
                _reference_leaf(reference)
                for reference in _expression_references(node.func, aliases)
            }
            if "_FitIsolation" in leaves:
                constructors.append(path.relative_to(PACKAGE_ROOT).as_posix())
    assert constructors == ["experiments/folds.py", "experiments/folds.py"]


def test_slice_3e_geometry_and_purge_are_derived_in_the_required_order() -> None:
    """Position arithmetic, outcome reach, purge, then embargo stay load-bearing."""
    path = PACKAGE_ROOT / "experiments" / "folds.py"
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    generator = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_generate_walk_forward_folds"
    )

    assignments: dict[str, ast.AST] = {}
    assignment_lines: dict[str, int] = {}
    for node in ast.walk(generator):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name):
            assignments[target.id] = node.value
            assignment_lines[target.id] = node.lineno
    expected_expressions = {
        "fit_start": "index * step_rows",
        "fit_stop": "fit_start + fit_rows",
        "eval_start": "fit_stop",
        "eval_stop": "eval_start + eval_rows",
        "derived_purge_rows": "min(fit_rows, maximum_forward_rows)",
        "derived_purge_start": "fit_stop - derived_purge_rows",
        "applied_purge_start": "fit_stop - applied_purge_rows",
        "after_purge": "tuple(range(fit_start, applied_purge_start))",
    }
    for name, expression in expected_expressions.items():
        assert ast.dump(assignments[name]) == ast.dump(ast.parse(expression, mode="eval").body)
    assert (
        assignment_lines["after_purge"]
        < assignment_lines["applied_embargo_ranges"]
        < assignment_lines["effective_positions"]
    )

    partial_refusal = ast.parse("if eval_stop > keys.size:\n    break\n").body[0]
    assert isinstance(partial_refusal, ast.If)
    assert any(
        isinstance(node, ast.If)
        and ast.dump(node.test) == ast.dump(partial_refusal.test)
        and any(isinstance(statement, ast.Break) for statement in node.body)
        for node in ast.walk(generator)
    )
    outcome_reaches = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_outcome_reaches"
    )
    assert "_forward_rows" in {
        _reference_leaf(reference)
        for reference in _expression_references(outcome_reaches, _symbol_aliases(tree))
    }

    forbidden_import_roots = {
        "calendar",
        "datetime",
        "dateutil",
        "pytz",
        "time",
        "zoneinfo",
    }
    assert (
        not {module.split(".", maxsplit=1)[0] for module in _absolute_import_modules(tree)}
        & forbidden_import_roots
    )


def test_slice_3e_fold_boundary_controls_are_test_only_and_cannot_be_softened() -> None:
    """CP-10's purge-disabled and overbroad-override controls must both fail."""
    source = SLICE_3E_FOLD_TEST_PATH.read_text()
    tree = ast.parse(source, filename=str(SLICE_3E_FOLD_TEST_PATH))
    observed = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in SLICE_3E_NEGATIVE_CONTROL_CLASSES
    }
    assert observed == SLICE_3E_NEGATIVE_CONTROL_CLASSES
    assert not _slice_2c_gate_evasion_violations(tree)

    rig = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_assert_no_undeclared_overlap"
    )
    rig_references = {
        _reference_leaf(reference)
        for reference in _expression_references(rig, _symbol_aliases(tree))
    }
    assert {"_effective_fit_positions", "_permitted_overlap_range"} <= rig_references
    assert any(isinstance(node, ast.Raise) for node in ast.walk(rig))

    tests = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }
    assert "PurgeDisabledFoldControl" in {
        _reference_leaf(reference)
        for reference in _expression_references(
            tests["test_default_fold_boundary_rig_catches_purge_disabled_control"],
            _symbol_aliases(tree),
        )
    }
    assert "OverbroadOverrideFoldControl" in {
        _reference_leaf(reference)
        for reference in _expression_references(
            tests["test_structured_override_permits_only_its_exact_recorded_overlap"],
            _symbol_aliases(tree),
        )
    }

    production_controls: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        production_tree = ast.parse(path.read_text(), filename=str(path))
        production_controls.extend(
            node.name
            for node in ast.walk(production_tree)
            if isinstance(node, ast.ClassDef) and node.name in SLICE_3E_NEGATIVE_CONTROL_CLASSES
        )
    assert not production_controls


def test_slice_3f_policy_modules_registry_and_schema_vocabulary_are_exact() -> None:
    """3F has three policies and one validated intent-schema literal, not a registry."""
    decisions_root = PACKAGE_ROOT / "decisions"
    inventory = {
        path.relative_to(decisions_root).as_posix() for path in decisions_root.rglob("*.py")
    }
    assert inventory == SLICE_3F_DECISION_INVENTORY

    execution_tree = ast.parse(
        (decisions_root / "execution.py").read_text(),
        filename=str(decisions_root / "execution.py"),
    )
    assert not _declared_exports(execution_tree)
    assert _module_level_public_names(execution_tree) == {"annotations"}

    reference_tree = ast.parse(
        (decisions_root / "reference.py").read_text(),
        filename=str(decisions_root / "reference.py"),
    )
    expected_classes = set(SLICE_3F_POLICY_CLASSES.values())
    assert _declared_exports(reference_tree) == expected_classes
    assert _module_level_public_names(reference_tree) == {
        *expected_classes,
        "annotations",
    }
    for class_name in expected_classes:
        assert (
            _class_public_surface(_only_class(reference_tree, class_name))
            == (PHASE3_PROTOCOL_SURFACES["DecisionPolicy"])
        )

    decisions = importlib.import_module(f"{ROOT_NAME}.decisions")
    assert decisions.DECISION_POLICIES.names() == frozenset(SLICE_3F_POLICY_CLASSES)
    assert (
        type(decisions.DECISION_POLICIES.create("abstain_v1")).__name__
        == (SLICE_3F_POLICY_CLASSES["abstain_v1"])
    )
    assert (
        type(decisions.DECISION_POLICIES.create("event_exposure_v1")).__name__
        == SLICE_3F_POLICY_CLASSES["event_exposure_v1"]
    )
    assert (
        type(
            decisions.DECISION_POLICIES.create(
                "threshold_v1",
                output_column="score",
                threshold=0.0,
            )
        ).__name__
        == SLICE_3F_POLICY_CLASSES["threshold_v1"]
    )

    frame_tree = ast.parse((PACKAGE_ROOT / "core" / "research_frames.py").read_text())
    schema_assignment = next(
        node
        for node in frame_tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_INTENT_SCHEMA_IDS"
            for target in node.targets
        )
    )
    assert isinstance(schema_assignment.value, ast.Call)
    assert isinstance(schema_assignment.value.func, ast.Name)
    assert schema_assignment.value.func.id == "frozenset"
    assert len(schema_assignment.value.args) == 1
    assert ast.literal_eval(schema_assignment.value.args[0]) == {
        "target_exposure_intent_v1",
        "bracketed_exposure_intent_v1",
    }
    assert not schema_assignment.value.keywords


def test_slice_3f_fan_out_has_one_inference_call_and_no_storage_authority() -> None:
    """Policy fan-out receives one frame and cannot infer again or persist intent."""
    path = PACKAGE_ROOT / "experiments" / "decisions.py"
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    aliases = _symbol_aliases(tree)
    assert not _declared_exports(tree)
    assert _module_level_public_names(tree) == {"annotations"}

    fan_out = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_fan_out_model_output"
    )
    assert not fan_out.args.args
    assert [argument.arg for argument in fan_out.args.kwonlyargs] == [
        "model_output",
        "policies",
    ]
    fan_out_references = {
        _reference_leaf(reference) for reference in _expression_references(fan_out, aliases)
    }
    assert {"_execute_policy_plans", "_policy_objects_and_plans"} <= (fan_out_references)
    assert not fan_out_references & {
        "FittedState",
        "ModelInputs",
        "ResearchModel",
        "fit_model",
        "infer_model",
    }
    execute_plans = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_execute_policy_plans"
    )
    execute_references = {
        _reference_leaf(reference) for reference in _expression_references(execute_plans, aliases)
    }
    assert {"_execute_policy", "_validate_policy_compatibility"} <= execute_references
    prepare_plans = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_policy_objects_and_plans"
    )
    assert "_snapshot_policy" in {
        _reference_leaf(reference) for reference in _expression_references(prepare_plans, aliases)
    }

    infer_once = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_infer_once_and_fan_out"
    )
    calls = [leaf for leaf, _line, _call in _function_call_records(infer_once, aliases)]
    assert calls.count("inference") == 1
    assert calls.count("_execute_policy_plans") == 1
    assert calls.count("_validate_policy_columns") == 1
    assert "infer_model" not in calls
    call_lines = {
        leaf: line
        for leaf, line, _call in _function_call_records(infer_once, aliases)
        if leaf in {"_validate_policy_columns", "inference", "_execute_policy_plans"}
    }
    assert call_lines["_validate_policy_columns"] < call_lines["inference"]
    assert call_lines["inference"] < call_lines["_execute_policy_plans"]

    forbidden_imports = {
        "fcntl",
        "os",
        "pathlib",
        "shutil",
        "trading_research.artifacts",
        "trading_research.data",
    }
    assert not (_absolute_import_modules(tree) & forbidden_imports)
    forbidden_calls = {
        "open",
        "put",
        "replace",
        "save",
        "savez",
        "write_bytes",
        "write_text",
    }
    assert (
        not {
            _reference_leaf(reference)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for reference in _expression_references(node.func, aliases)
        }
        & forbidden_calls
    )


def test_slice_3f_trichotomy_round_trip_and_alignment_are_release_guards() -> None:
    """The exact sparse trichotomy tests and in-memory codec cannot be softened."""
    source = SLICE_3F_DECISION_TEST_PATH.read_text()
    tree = ast.parse(source, filename=str(SLICE_3F_DECISION_TEST_PATH))
    assert not _slice_2c_gate_evasion_violations(tree)
    tests = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }
    required = {
        "test_canonical_round_trip_preserves_absent_abstain_and_flat_bits",
        "test_n_policy_fan_out_performs_one_inference_and_zero_reinference",
        "test_sparse_consumer_alignment_keeps_presence_separate_from_nan",
        "test_unknown_intent_schema_is_refused",
    }
    assert required <= tests.keys()

    round_trip_references = {
        _reference_leaf(reference)
        for reference in _expression_references(
            tests["test_canonical_round_trip_preserves_absent_abstain_and_flat_bits"],
            _symbol_aliases(tree),
        )
    }
    assert {"_decode_intent_frame", "_encode_intent_frame", "uint64"} <= (round_trip_references)
    alignment_references = {
        _reference_leaf(reference)
        for reference in _expression_references(
            tests["test_sparse_consumer_alignment_keeps_presence_separate_from_nan"],
            _symbol_aliases(tree),
        )
    }
    assert {"_align_intent_to_dense_axis", "_present", "_target_exposure"} <= (alignment_references)

    intent_paths = (
        PACKAGE_ROOT / "core" / "research_frames.py",
        PACKAGE_ROOT / "decisions" / "execution.py",
        PACKAGE_ROOT / "decisions" / "reference.py",
        PACKAGE_ROOT / "experiments" / "decisions.py",
    )
    for path in intent_paths:
        intent_tree = ast.parse(path.read_text(), filename=str(path))
        assert not (
            _absolute_import_modules(intent_tree)
            & {
                "trading_research.artifacts.materialization",
                "trading_research.artifacts.store",
            }
        )


def test_slice_4e_evaluator_modules_registries_and_parameters_are_exact() -> None:
    """Plane A stays closed and Plane B adds exactly its two accepted plugins."""
    evaluation_root = PACKAGE_ROOT / "evaluation"
    inventory = {
        path.relative_to(evaluation_root).as_posix() for path in evaluation_root.rglob("*.py")
    }
    assert inventory == SLICE_3G_EVALUATION_INVENTORY

    execution_tree = ast.parse(
        (evaluation_root / "execution.py").read_text(),
        filename=str(evaluation_root / "execution.py"),
    )
    assert not _declared_exports(execution_tree)
    assert _module_level_public_names(execution_tree) == {"annotations"}

    init_tree = ast.parse(
        (evaluation_root / "__init__.py").read_text(),
        filename=str(evaluation_root / "__init__.py"),
    )
    registry_tree = ast.parse(
        (evaluation_root / "registry.py").read_text(),
        filename=str(evaluation_root / "registry.py"),
    )
    assert _declared_exports(init_tree) == {"EVALUATORS", "TRADE_EVALUATORS"}
    assert _module_level_public_names(init_tree) == {"EVALUATORS", "TRADE_EVALUATORS"}
    assert _declared_exports(registry_tree) == {"EVALUATORS", "TRADE_EVALUATORS"}
    assert _module_level_public_names(registry_tree) == {
        "EVALUATORS",
        "Evaluator",
        "Registry",
        "TRADE_EVALUATORS",
        "TradeEvaluator",
    }

    reference_tree = ast.parse(
        (evaluation_root / "reference.py").read_text(),
        filename=str(evaluation_root / "reference.py"),
    )
    expected_classes = set(SLICE_3G_EVALUATOR_CLASSES.values())
    assert _declared_exports(reference_tree) == expected_classes
    assert _module_level_public_names(reference_tree) == {
        *expected_classes,
        "annotations",
    }
    for class_name in expected_classes:
        component = _only_class(reference_tree, class_name)
        assert _class_public_surface(component) == PHASE3_PROTOCOL_SURFACES["Evaluator"]
        constructor = next(
            node
            for node in component.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        assert [argument.arg for argument in constructor.args.args] == ["self"]
        assert [argument.arg for argument in constructor.args.kwonlyargs] == [
            "output_column",
            "outcome_column",
            "min_effective_samples",
        ]
        assert constructor.args.kw_defaults == [None, None, None]
        assert not constructor.args.defaults
        assert constructor.args.vararg is None
        assert constructor.args.kwarg is None

    trade_reference_tree = ast.parse(
        (evaluation_root / "trade_reference.py").read_text(),
        filename=str(evaluation_root / "trade_reference.py"),
    )
    expected_trade_classes = set(SLICE_4E_TRADE_EVALUATOR_CLASSES.values())
    assert _declared_exports(trade_reference_tree) == expected_trade_classes
    assert _module_level_public_names(trade_reference_tree) == {
        *expected_trade_classes,
        "annotations",
    }
    expected_constructor_fields = {
        "EquityDrawdown": ["min_effective_rows"],
        "GrossRSummary": ["min_effective_trades", "risk_distance"],
        "TradeSummary": ["min_effective_trades"],
    }
    for class_name in expected_trade_classes:
        component = _only_class(trade_reference_tree, class_name)
        assert _class_public_surface(component) == PHASE3_PROTOCOL_SURFACES["TradeEvaluator"]
        constructor = next(
            node
            for node in component.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        assert [argument.arg for argument in constructor.args.args] == ["self"]
        assert [argument.arg for argument in constructor.args.kwonlyargs] == (
            expected_constructor_fields[class_name]
        )
        assert constructor.args.kw_defaults == [None] * len(expected_constructor_fields[class_name])
        assert not constructor.args.defaults
        assert constructor.args.vararg is None
        assert constructor.args.kwarg is None

    evaluation = importlib.import_module(f"{ROOT_NAME}.evaluation")
    assert evaluation.EVALUATORS.names() == frozenset(SLICE_3G_EVALUATOR_CLASSES)
    assert evaluation.TRADE_EVALUATORS.names() == frozenset(SLICE_4E_TRADE_EVALUATOR_CLASSES)
    assert (
        type(
            evaluation.EVALUATORS.create(
                "rank_ic_v1",
                output_column="score",
                outcome_column="target.forward_log_return",
                min_effective_samples=2,
            )
        ).__name__
        == SLICE_3G_EVALUATOR_CLASSES["rank_ic_v1"]
    )
    assert (
        type(
            evaluation.EVALUATORS.create(
                "sign_accuracy_v1",
                output_column="score",
                outcome_column="target.forward_log_return",
                min_effective_samples=1,
            )
        ).__name__
        == SLICE_3G_EVALUATOR_CLASSES["sign_accuracy_v1"]
    )


def test_slice_3g_reference_semantics_and_edge_goldens_are_pinned() -> None:
    """Average ranks and strict non-zero signs cannot drift to library defaults."""
    reference_path = PACKAGE_ROOT / "evaluation" / "reference.py"
    reference_tree = ast.parse(
        reference_path.read_text(),
        filename=str(reference_path),
    )
    aliases = _symbol_aliases(reference_tree)
    references = {
        _reference_leaf(reference) for reference in _expression_references(reference_tree, aliases)
    }
    assert not references & {"corrcoef", "rankdata", "spearmanr"}
    assert not {
        module.split(".", maxsplit=1)[0] for module in _absolute_import_modules(reference_tree)
    } & {"pandas", "polars", "scipy"}

    average_ranks = next(
        node
        for node in reference_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_average_ranks"
    )
    sort_calls = [
        node
        for node in ast.walk(average_ranks)
        if isinstance(node, ast.Call)
        and "argsort"
        in {_reference_leaf(reference) for reference in _expression_references(node.func, aliases)}
    ]
    assert len(sort_calls) == 1
    assert {keyword.arg: ast.literal_eval(keyword.value) for keyword in sort_calls[0].keywords} == {
        "kind": "mergesort"
    }

    rank_correlation = next(
        node
        for node in reference_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_rank_correlation"
    )
    rank_references = {
        _reference_leaf(reference)
        for reference in _expression_references(rank_correlation, aliases)
    }
    assert {"_average_ranks", "dot", "sqrt"} <= rank_references

    sign = _only_class(reference_tree, "SignAccuracy")
    sign_evaluate = next(
        node for node in sign.body if isinstance(node, ast.FunctionDef) and node.name == "evaluate"
    )
    comparisons = {
        ast.dump(node) for node in ast.walk(sign_evaluate) if isinstance(node, ast.Compare)
    }
    for expression in (
        "output != 0.0",
        "outcome != 0.0",
        "(left > 0.0) == (right > 0.0)",
    ):
        assert ast.dump(ast.parse(expression, mode="eval").body) in comparisons

    test_source = SLICE_3G_EVALUATION_TEST_PATH.read_text()
    test_tree = ast.parse(
        test_source,
        filename=str(SLICE_3G_EVALUATION_TEST_PATH),
    )
    assert not _slice_2c_gate_evasion_violations(test_tree)
    tests = {
        node.name
        for node in test_tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }
    assert {
        "test_average_tie_ranks_are_computed_explicitly",
        "test_rank_ic_tie_golden_and_effective_count",
        "test_rank_ic_undefined_edge_cases_return_nan_with_honest_count",
        "test_sign_accuracy_excludes_nan_exact_zero_and_signed_zero",
        "test_sign_accuracy_below_minimum_returns_nan",
        "test_sparse_key_alignment_occurs_before_pair_filtering",
    } <= tests


def test_slice_3g_inventory_has_exact_plane_a_axes_and_early_preflight() -> None:
    """Inventory is F x S x E; policy work is recorded outside that identity."""
    path = PACKAGE_ROOT / "experiments" / "evaluation.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    aliases = _symbol_aliases(tree)
    assert not _declared_exports(tree)
    assert _module_level_public_names(tree) == {"annotations"}

    entry = _only_class(tree, "_EvaluationInventoryEntry")
    entry_fields = {
        node.target.id
        for node in entry.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert entry_fields == {
        "_comparison_tolerance",
        "_evaluated_output_columns",
        "_evaluator_origin",
        "_fitted_state_identity_key",
        "_horizon_rows",
        "_decision_delay_rows",
        "_metric_names",
        "_metrics",
        "_model_output_artifact_identity_key",
        "_outcome_origin",
    }
    assert not any("policy" in field for field in entry_fields)

    evaluate = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_evaluate_plane_a_inventory"
    )
    assert not evaluate.args.args
    assert [argument.arg for argument in evaluate.args.kwonlyargs] == [
        "fitted_outputs",
        "outcome_targets",
        "evaluators",
        "policy_executions",
    ]
    assert evaluate.args.kw_defaults == [None, None, None, None]
    expected_assignment = next(
        node
        for node in ast.walk(evaluate)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "expected" for target in node.targets)
    )
    assert ast.dump(expected_assignment.value) == ast.dump(
        ast.parse("len(outputs) * len(targets) * len(plans)", mode="eval").body
    )
    call_records = _function_call_records(evaluate, aliases)
    validation_lines = [line for leaf, line, _call in call_records if leaf == "_validate_artifacts"]
    execution_lines = [line for leaf, line, _call in call_records if leaf == "_execute_evaluator"]
    assert len(validation_lines) == 1
    assert len(execution_lines) == 1
    assert validation_lines[0] < execution_lines[0]
    assert not {
        _reference_leaf(reference) for reference in _expression_references(evaluate, aliases)
    } & {"IntentFrame", "DecisionPolicy", "decide"}

    preflight = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_preflight_plane_a"
    )
    preflight_calls = {leaf for leaf, _line, _call in _function_call_records(preflight, aliases)}
    assert {"_snapshot_evaluator", "_validate_evaluator_columns"} <= preflight_calls
    assert not preflight_calls & {
        "compute_features",
        "compute_outcomes",
        "encode_representation",
        "fit_model",
        "fit_representation",
        "infer_model",
    }

    tests = {
        node.name
        for node in ast.parse(SLICE_3G_EVALUATION_TEST_PATH.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }
    assert {
        "test_descriptive_and_pre_evaluation_failure_records_have_zero_trials",
        "test_plane_a_inventory_is_exactly_f_times_s_times_e_without_policy_axis",
        "test_requirement_mismatch_refuses_before_evaluator_or_inference_work",
    } <= tests


def test_slice_3g_has_no_upstream_recomputation_or_deferred_evaluation_plane() -> None:
    """Evaluator code sees artifacts only and no deferred statistic exists."""
    forbidden_statistics = {
        "decision-plane",
        "decision_plane",
        "deflated",
        "drawdown",
        "pnl",
        "quantile_spread",
        "sharpe",
        "turnover",
    }
    violations: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        lower_source = path.read_text().lower()
        for term in sorted(forbidden_statistics):
            if path == PACKAGE_ROOT / "core" / "simulation.py" and term == "pnl":
                # Slice 4B's accepted TradeLog/EquityCurve contracts name their
                # money columns.
                continue
            if path == PACKAGE_ROOT / "artifacts" / "container.py" and term == "deflated":
                # Oversized run-frame ZIP compression is an artifact-envelope
                # mode, not the prohibited deferred performance statistic.
                continue
            if path in {
                PACKAGE_ROOT / "simulation" / "bracket.py",
                PACKAGE_ROOT / "evaluation" / "trade_reference.py",
                PACKAGE_ROOT / "experiments" / "orchestration.py",
                PACKAGE_ROOT / "experiments" / "uls_entry_bundles.py",
                PACKAGE_ROOT / "experiments" / "uls_joint_bundles.py",
                PACKAGE_ROOT / "experiments" / "uls_tp_bundles.py",
                PACKAGE_ROOT / "experiments" / "uls_tp_oof.py",
                PACKAGE_ROOT / "ops" / "phase7_validation.py",
                PACKAGE_ROOT / "ops" / "phase8_entry_validation.py",
                PACKAGE_ROOT / "ops" / "phase8_joint_validation.py",
                PACKAGE_ROOT / "ops" / "phase8_tp_validation.py",
            } and term in {"drawdown", "pnl"}:
                # Slices 4C-4F implement/persist the accepted money arithmetic
                # and sole drawdown evaluator, not deferred statistics.
                continue
            if term in lower_source:
                violations.append(f"{path.relative_to(PACKAGE_ROOT)} contains deferred {term!r}")

    forbidden_upstream = {
        "IntentFrame",
        "compute_features",
        "compute_outcomes",
        "decide",
        "encode_representation",
        "fit_model",
        "fit_representation",
        "infer_model",
    }
    for path in _layer_files("evaluation"):
        tree = ast.parse(path.read_text(), filename=str(path))
        references = {
            _reference_leaf(reference)
            for reference in _expression_references(tree, _symbol_aliases(tree))
        }
        for name in sorted(references & forbidden_upstream):
            violations.append(f"{path.relative_to(PACKAGE_ROOT)} references {name}")
    assert not violations, "Slice 3G authority/scope violations: " + "; ".join(violations)


def test_slice_3h_run_modules_state_vocabulary_and_layout_are_exact() -> None:
    """3H adds two cohesive run modules and the one closed state machine."""
    experiments_root = PACKAGE_ROOT / "experiments"
    inventory = {
        path.relative_to(experiments_root).as_posix() for path in experiments_root.rglob("*.py")
    }
    assert inventory == PHASE3_EXPERIMENT_INVENTORY

    identity_tree = ast.parse(
        (experiments_root / "run_identity.py").read_text(),
        filename=str(experiments_root / "run_identity.py"),
    )
    runs_tree = ast.parse(
        (experiments_root / "runs.py").read_text(),
        filename=str(experiments_root / "runs.py"),
    )
    assert not _declared_exports(identity_tree)
    assert _module_level_public_names(identity_tree) == {"annotations"}
    assert _declared_exports(runs_tree) == SLICE_3H_RUN_EXPORTS
    assert _module_level_public_names(runs_tree) == {
        *SLICE_3H_RUN_EXPORTS,
        "annotations",
    }

    runs = importlib.import_module(f"{ROOT_NAME}.experiments.runs")
    assert {state.name for state in runs.RunState} == SLICE_3H_RUN_STATES
    assert {state.value for state in runs.RunState} == SLICE_3H_RUN_STATES

    root_files = next(
        node
        for node in runs_tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_ROOT_FILES" for target in node.targets
        )
    )
    assert isinstance(root_files.value, ast.Call)
    assert ast.literal_eval(root_files.value.args[0]) == {
        "config.resolved.json",
        "determinism_report.json",
        "evaluation_inventory.json",
        "heartbeat.json",
        "identity.json",
        "manifest.json",
        "state.json",
    }
    payload_directories = next(
        node
        for node in runs_tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_PAYLOAD_DIRECTORIES"
            for target in node.targets
        )
    )
    assert ast.literal_eval(payload_directories.value) == (
        "artifacts",
        "logs",
        "metrics",
    )

    manifest = _only_class(runs_tree, "RunManifest")
    manifest_fields = {
        node.target.id
        for node in manifest.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert manifest_fields == {
        "access_audit_refs",
        "artifact_hashes",
        "binding_digest",
        "binding_id",
        "canonical",
        "cost_semantics_unverified",
        "created_unix_ns",
        "declared_lockbox_evaluation",
        "dependency_lock_sha256",
        "evaluation_count",
        "execution_price_model",
        "experiment_fingerprint",
        "failure_reason",
        "finished_unix_ns",
        "lockbox_touched",
        "log_hashes",
        "metric_hashes",
        "oos_touched",
        "outcome_class",
        "platform_identity",
        "policy_execution_json",
        "root_file_hashes",
        "run_id",
        "started_unix_ns",
        "state",
    }
    assert "attempt_number" not in manifest_fields

    scan = next(
        node
        for node in runs_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "scan_runs"
    )
    assert [argument.arg for argument in scan.args.args] == ["runs_root"]
    assert [argument.arg for argument in scan.args.kwonlyargs] == [
        "now_unix_ns",
        "stale_after_ns",
        "reconcile",
    ]
    assert len(scan.args.kw_defaults) == 3
    assert scan.args.kw_defaults[:2] == [None, None]
    assert isinstance(scan.args.kw_defaults[2], ast.Constant)
    assert scan.args.kw_defaults[2].value is True


def test_slice_3h_fingerprint_inputs_are_closed_pure_and_machine_excluding() -> None:
    """The hash has every ADR-0011 input and no execution or machine identity."""
    path = PACKAGE_ROOT / "experiments" / "run_identity.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    aliases = _symbol_aliases(tree)
    identity_class = _only_class(tree, "_ExperimentIdentity")
    constructor = next(
        node
        for node in identity_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    fingerprint_assignment = next(
        node
        for node in constructor.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "fingerprint_inputs"
            for target in node.targets
        )
    )
    assert isinstance(fingerprint_assignment.value, ast.Dict)
    fingerprint_keys = {
        key.value
        for key in fingerprint_assignment.value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    assert fingerprint_keys == {
        "algorithm",
        "components",
        "dataset_identities",
        "declared_lockbox_evaluation",
        "dependency_lock_sha256",
        "platform",
        "resolved_config",
        "seed",
    }
    fingerprint_references = {
        _reference_leaf(reference)
        for reference in _expression_references(fingerprint_assignment.value, aliases)
    }
    assert not fingerprint_references & {
        "machine_document",
        "machine_identity",
        "run_id",
        "time_ns",
        "urandom",
    }
    constructor_calls = {
        leaf for leaf, _line, _call in _function_call_records(constructor, aliases)
    }
    assert not constructor_calls & {"open", "time_ns", "urandom"}

    component_kinds = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_COMPONENT_KINDS"
            for target in node.targets
        )
    )
    assert isinstance(component_kinds.value, ast.Call)
    assert ast.literal_eval(component_kinds.value.args[0]) == {
        "corpus_research_model",
        "decision_policy",
        "evaluator",
        "execution_spec",
        "feature_provider",
        "outcome_spec",
        "representation_model",
        "research_model",
    }
    algorithm = _slice_2e_literal_assignment(tree, "_FINGERPRINT_ALGORITHM")
    assert algorithm == "experiment-fingerprint-v1"
    mint = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_mint_run_id"
    )
    assert {"time_ns", "urandom", "_run_id_from_parts"} <= {
        leaf for leaf, _line, _call in _function_call_records(mint, aliases)
    }


def test_slice_3h_state_writes_are_descriptor_relative_and_terminally_sealed() -> None:
    """Mutable state cannot escape its pinned run directory or survive terminal."""
    path = PACKAGE_ROOT / "experiments" / "runs.py"
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    aliases = _symbol_aliases(tree)
    assert f"{ROOT_NAME}.data.atomic_write" not in _absolute_import_modules(tree)
    assert "write_text" not in source
    assert "write_bytes" not in source

    replace = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_atomic_replace_bytes_at"
    )
    allowed_sets = {
        frozenset(
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        )
        for node in ast.walk(replace)
        if isinstance(node, ast.Set)
    }
    assert frozenset({"heartbeat.json", "state.json"}) in allowed_sets
    replace_calls = {leaf for leaf, _line, _call in _function_call_records(replace, aliases)}
    assert {"replace", "fsync", "_create_temp_file", "_write_complete"} <= (replace_calls)

    heartbeat = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_touch_file_at"
    )
    heartbeat_calls = {leaf for leaf, _line, _call in _function_call_records(heartbeat, aliases)}
    assert {"utime", "fsync", "_safe_stat_at"} <= heartbeat_calls
    seal = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_seal_run_directory"
    )
    assert {"chmod", "fchmod", "fsync"} <= {
        leaf for leaf, _line, _call in _function_call_records(seal, aliases)
    }

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = {
            _reference_leaf(reference) for reference in _expression_references(node.func, aliases)
        }
        if called & {"link", "rename", "replace", "utime"}:
            keywords = {keyword.arg for keyword in node.keywords}
            assert keywords & {"dir_fd", "src_dir_fd"}


def test_slice_3h_attempts_scan_and_interruption_are_files_only_guards() -> None:
    """Attempts are derived, stale runs persist, and SQLite remains absent."""
    run_paths = (
        PACKAGE_ROOT / "experiments" / "run_identity.py",
        PACKAGE_ROOT / "experiments" / "runs.py",
    )
    for path in run_paths:
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        assert "sqlite" not in source.lower()
        assert "datetime" not in source.lower()
        assert "zoneinfo" not in source.lower()
        assert "/path/to/workspace" not in source
        assert "attempt_number" not in {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }

    runs_tree = ast.parse(run_paths[1].read_text(), filename=str(run_paths[1]))
    aliases = _symbol_aliases(runs_tree)
    scan = next(
        node
        for node in runs_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "scan_runs"
    )
    scan_references = {
        _reference_leaf(reference) for reference in _expression_references(scan, aliases)
    }
    assert {"_scan_run_projections", "_RunRecord"} <= scan_references
    projection_scan = next(
        node
        for node in runs_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_scan_run_projections"
    )
    projection_references = {
        _reference_leaf(reference) for reference in _expression_references(projection_scan, aliases)
    }
    assert {"_open_run_collection", "_projection_pass", "_RunProjectionScan"} <= (
        projection_references
    )
    assert not scan_references & {"Registry", "connect", "execute", "sqlite3"}
    assert not projection_references & {"Registry", "connect", "execute", "sqlite3"}

    test_source = SLICE_3H_RUN_TEST_PATH.read_text()
    test_tree = ast.parse(test_source, filename=str(SLICE_3H_RUN_TEST_PATH))
    assert not _slice_2c_gate_evasion_violations(test_tree)
    assert f"{ROOT_NAME}.data" not in _absolute_import_modules(test_tree)
    assert "/path/to/workspace" not in test_source
    tests = {
        node.name: node
        for node in test_tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }
    required = {
        "test_concurrent_launches_get_unique_query_time_attempt_numbers",
        "test_determinism_report_is_immutable_and_manifest_hashed",
        "test_every_state_transition_and_terminal_immutability_are_enforced",
        "test_evaluated_inventory_survives_a_later_interruption",
        "test_experiment_fingerprint_sensitivity_and_insensitivity_matrix",
        "test_kill_9_becomes_interrupted_on_next_scan",
        "test_run_record_scan_golden_reconstructs_all_known_states_from_files",
        "test_run_state_writes_cannot_escape_through_symlinks",
    }
    assert required <= tests.keys()
    kill_references = {
        _reference_leaf(reference)
        for reference in _expression_references(
            tests["test_kill_9_becomes_interrupted_on_next_scan"],
            _symbol_aliases(test_tree),
        )
    }
    assert {"SIGKILL", "kill", "scan_runs"} <= kill_references
    concurrent_references = {
        _reference_leaf(reference)
        for reference in _expression_references(
            tests["test_concurrent_launches_get_unique_query_time_attempt_numbers"],
            _symbol_aliases(test_tree),
        )
    }
    assert {"ThreadPoolExecutor", "scan_runs"} <= concurrent_references


def test_slice_3i_modules_have_closed_inventory_and_public_surfaces() -> None:
    """The composition root adds only the approved config, API and CLI surface."""
    experiments_root = PACKAGE_ROOT / "experiments"
    inventory = {
        path.relative_to(experiments_root).as_posix() for path in experiments_root.rglob("*.py")
    }
    assert inventory == PHASE3_EXPERIMENT_INVENTORY

    for relative, public_names in SLICE_3I_PUBLIC_SURFACES.items():
        path = experiments_root / relative
        tree = ast.parse(path.read_text(), filename=str(path))
        assert _declared_exports(tree) == SLICE_3I_DECLARED_EXPORTS[relative]
        assert _module_level_public_names(tree) == public_names
        module_name = relative.removesuffix(".py").replace("/", ".")
        module = importlib.import_module(f"{ROOT_NAME}.experiments.{module_name}")
        assert set(_runtime_public_bindings(module)) == public_names


def test_slice_3i_config_schema_and_cli_vocabulary_are_closed() -> None:
    """Resolved config and command-line vocabulary cannot grow accidentally."""
    experiments_root = PACKAGE_ROOT / "experiments"
    config_path = experiments_root / "config.py"
    config_tree = ast.parse(config_path.read_text(), filename=str(config_path))
    parser_class = _only_class(config_tree, "ResolvedExperimentConfig")
    parser = next(
        node
        for node in parser_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "_from_document"
    )
    root_call = min(
        (
            node
            for node in ast.walk(parser)
            if isinstance(node, ast.Call) and _qualified_name(node.func) == "_exact_dict"
        ),
        key=lambda node: node.lineno,
    )
    fields = next(keyword.value for keyword in root_call.keywords if keyword.arg == "fields")
    assert ast.literal_eval(fields) == {
        "access_reason",
        "bounds",
        "dataset",
        "declared_lockbox_evaluation",
        "evaluation_outcomes",
        "evaluators",
        "execution",
        "features",
        "folds",
        "integrity_mode",
        "model",
        "policies",
        "representation",
        "schema",
        "seed",
        "split_policy",
        "training_outcome",
        "trade_evaluators",
    }

    cli_path = experiments_root / "cli.py"
    cli_tree = ast.parse(cli_path.read_text(), filename=str(cli_path))
    cli_parser = next(
        node
        for node in cli_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_parser"
    )
    verbs = {
        node.args[0].value
        for node in ast.walk(cli_parser)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_parser"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert verbs == SLICE_3I_CLI_VERBS
    for relative in ("cli.py", "worker.py", "__main__.py"):
        source = (experiments_root / relative).read_text().lower()
        assert "tolerance" not in source
        assert "comparison" not in source


def test_slice_3i_verification_is_two_attempts_forced_cache_bypass_and_stored() -> None:
    """Run verification recomputes twice and publishes one report to both attempts."""
    path = PACKAGE_ROOT / "experiments" / "orchestration.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    aliases = _symbol_aliases(tree)
    verifier = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "verify_experiment"
    )
    records = _function_call_records(verifier, aliases)
    calls_by_leaf: dict[str, list[ast.Call]] = {}
    for leaf, _line, call in records:
        calls_by_leaf.setdefault(leaf, []).append(call)
    assert len(calls_by_leaf.get("_register_new_run", [])) == 2
    assert len(calls_by_leaf.get("_execute_registered_run", [])) == 2
    assert len(calls_by_leaf.get("_record_determinism_report", [])) == 2
    for call in calls_by_leaf["_execute_registered_run"]:
        verification = next(
            keyword.value for keyword in call.keywords if keyword.arg == "verification"
        )
        assert isinstance(verification, ast.Constant)
        assert verification.value is True

    pipeline = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_execute_pipeline"
    )
    pipeline_calls = {leaf for leaf, _line, _call in _function_call_records(pipeline, aliases)}
    assert {
        "verify_determinism",
        "materialize_features",
        "_upgrade_ephemeral_for_downstream",
    } <= pipeline_calls


def test_slice_3i_launchers_and_scope_boundaries_are_exact() -> None:
    """Detached execution uses argv vectors and Phase 3 gains no deferred machinery."""
    experiments_root = PACKAGE_ROOT / "experiments"
    cli_path = experiments_root / "cli.py"
    cli_source = cli_path.read_text()
    cli_tree = ast.parse(cli_source, filename=str(cli_path))
    launch_result = next(
        node
        for node in cli_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_launch_result"
    )
    subprocess_calls = [
        call
        for leaf, _line, call in _function_call_records(
            launch_result,
            _symbol_aliases(cli_tree),
        )
        if leaf == "run"
    ]
    assert len(subprocess_calls) == 1
    assert "shell" not in {keyword.arg for keyword in subprocess_calls[0].keywords}
    assert {"systemd-run", "--user", "--collect", "tmux", "new-session", "-d"} <= {
        node.value
        for node in ast.walk(cli_tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    forbidden_imports = {"datetime", "duckdb", "pandas", "polars", "pyarrow", "sqlite3", "zoneinfo"}
    forbidden_terms = {"commissionspec", "slippagespec"}
    violations: list[str] = []
    for relative in SLICE_3I_PUBLIC_SURFACES:
        path = experiments_root / relative
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        imported = _imported_module_roots(tree) & forbidden_imports
        violations.extend(f"{relative} imports {name}" for name in sorted(imported))
        lowered = source.lower()
        violations.extend(
            f"{relative} contains deferred {name}"
            for name in sorted(forbidden_terms)
            if name in lowered
        )
        permitted_phase4 = {
            "config.py": {"executionspec"},
            "orchestration.py": {"equitycurve", "executionspec", "tradelog"},
        }
        phase4_terms = {"equitycurve", "executionspec", "tradelog"}
        violations.extend(
            f"{relative} contains Phase-4 contract outside accepted surface {name}"
            for name in sorted(phase4_terms - permitted_phase4.get(relative, set()))
            if name in lowered
        )
        if "sqlite" in lowered:
            violations.append(f"{relative} contains SQLite vocabulary")
        for kind, identifier in _identifier_records(tree):
            if "utc" in identifier.lower().strip("_").split("_"):
                violations.append(f"{relative} {kind} declares UTC name {identifier!r}")
    assert not violations, "Slice 3I scope violations: " + "; ".join(violations)


def test_slice_3j_qualification_matrix_is_exact_active_and_end_to_end() -> None:
    """Closeout exercises the six approved loops without skip or gate escape."""
    path = SLICE_3J_QUALIFICATION_TEST_PATH
    assert path.is_file()
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "PHASE3_QUALIFICATION_USE_CASES"
            for target in node.targets
        )
    )
    assert isinstance(assignment.value, ast.Call)
    assert ast.literal_eval(assignment.value.args[0]) == SLICE_3J_USE_CASES
    assert not _slice_2c_gate_evasion_violations(tree)
    assert "pytest.skip" not in source
    assert "pytest.xfail" not in source

    qualification = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_all_six_supported_phase3_use_cases_complete_end_to_end"
    )
    calls = {
        leaf
        for leaf, _line, _call in _function_call_records(
            qualification,
            _symbol_aliases(tree),
        )
    }
    assert {"run_experiment", "verify_experiment", "scan_runs"} <= calls
    assert "_config_document" in calls
    assert {
        "supervised_mean_v1",
        "reference_library_v1",
        "trailing_standardizer_v1",
        "abstain_v1",
    } <= {
        node.value
        for node in ast.walk(qualification)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_slice_3j_performance_harness_pins_the_train_only_protocol() -> None:
    """The 30-second budget uses one warm-up, five runs and rejecting audit."""
    path = SLICE_3J_PERFORMANCE_TEST_PATH
    assert path.is_file()
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    assignments = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assignments["_TIMED_REPETITIONS"]) == 5
    assert ast.literal_eval(assignments["_BUDGET_SECONDS"]) == 30.0
    assert _only_class(tree, "_RejectingAuditWriter")
    assert "pytest.skip" not in source
    assert "pytest.xfail" not in source
    assert f"{ROOT_NAME}.simulation" not in _absolute_import_modules(tree)
    pytestmark = assignments["pytestmark"]
    assert _qualified_name(pytestmark) == "pytest.mark.performance"

    config_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_config_document"
    )
    returned = next(node.value for node in config_function.body if isinstance(node, ast.Return))
    assert isinstance(returned, ast.Dict)
    config_fields = {
        key.value: value
        for key, value in zip(returned.keys, returned.values, strict=True)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    assert isinstance(config_fields["access_reason"], ast.Constant)
    assert config_fields["access_reason"].value is None
    assert ast.literal_eval(config_fields["declared_lockbox_evaluation"]) is False
    dataset = config_fields["dataset"]
    assert isinstance(dataset, ast.Dict)
    dataset_fields = {
        key.value: value
        for key, value in zip(dataset.keys, dataset.values, strict=True)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    assert _qualified_name(dataset_fields["role"]) == "TRAIN_REF.role.value"

    performance_test = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_published_train_config_to_stored_run_meets_budget"
    )
    calls = [
        leaf
        for leaf, _line, _call in _function_call_records(
            performance_test,
            _symbol_aliases(tree),
        )
    ]
    assert calls.count("_run_once") == 2
    assert calls.count("_audit_snapshot") == 2
    assert {"_clean_committed_head", "_peak_rss_bytes", "_render_evidence"} <= set(calls)
    run_once = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_once"
    )
    run_calls = {
        leaf for leaf, _line, _call in _function_call_records(run_once, _symbol_aliases(tree))
    }
    assert {"load_resolved_config", "run_experiment", "scan_runs"} <= run_calls
    assert {
        "artifact_hashes",
        "artifact_sizes",
        "experiment_fingerprint",
        "feature_identity_key",
        "git_commit",
        "inventory_row_count",
        "metric_hashes",
        "peak_rss_bytes",
        "samples_seconds",
    } <= {
        node.value
        for node in ast.walk(performance_test)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    runs_path = PACKAGE_ROOT / "experiments" / "runs.py"
    runs_tree = ast.parse(runs_path.read_text(), filename=str(runs_path))
    limit = next(
        node.value
        for node in runs_tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_RUN_FILE_LIMIT_BYTES"
            for target in node.targets
        )
    )
    assert ast.literal_eval(limit) == 134_217_728
    for function_name in ("_publish_immutable_bytes_at", "_read_regular_bytes_at"):
        function = next(
            node
            for node in runs_tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        references = {
            _reference_leaf(reference)
            for reference in _expression_references(function, _symbol_aliases(runs_tree))
        }
        assert "_RUN_FILE_LIMIT_BYTES" in references


def test_slice_1d_loader_contains_an_explicit_raw_role_refusal() -> None:
    """RAW remains administrative source data, not a row-loadable target."""
    path = PACKAGE_ROOT / "data" / "integrity.py"
    if not path.is_file():
        return
    tree = ast.parse(path.read_text(), filename=str(path))
    aliases = _symbol_aliases(tree)
    guarded_refusal = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test_references = _expression_references(node.test, aliases)
            mentions_raw = any(
                _reference_leaf(reference).lower() == "raw" for reference in test_references
            )
            if mentions_raw and _body_contains_raise(node.body):
                guarded_refusal = True
        elif isinstance(node, ast.match_case):
            case_references = _expression_references(node.pattern, aliases)
            if node.guard is not None:
                case_references.update(_expression_references(node.guard, aliases))
            mentions_raw = any(
                _reference_leaf(reference).lower() == "raw" for reference in case_references
            )
            if mentions_raw and _body_contains_raise(node.body):
                guarded_refusal = True
    assert guarded_refusal, "data/integrity.py must explicitly raise for ViewRole.RAW"


# --------------------------------------------------------- Slice 1G GapIndex boundary


def test_gap_index_module_and_contract_surfaces_are_exact() -> None:
    """GapIndex adds one contract and no convenience producer or behavior."""
    path = PACKAGE_ROOT / "data" / "gaps.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    gap_index = _gap_index_class(tree)
    assert gap_index is not None, "data/gaps.py must define exactly one GapIndex"

    public_members: set[str] = set()
    for member in gap_index.body:
        if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
            if not member.name.startswith("_") or (
                member.name.startswith("__")
                and member.name.endswith("__")
                and member.name != "__init__"
            ):
                public_members.add(member.name)
        elif isinstance(member, ast.Assign):
            public_members.update(
                target.id
                for target in member.targets
                if isinstance(target, ast.Name)
                and (
                    not target.id.startswith("_")
                    or (
                        target.id.startswith("__")
                        and target.id.endswith("__")
                        and target.id != "__init__"
                    )
                )
            )
        elif (
            isinstance(member, ast.AnnAssign)
            and isinstance(member.target, ast.Name)
            and (
                not member.target.id.startswith("_")
                or (
                    member.target.id.startswith("__")
                    and member.target.id.endswith("__")
                    and member.target.id != "__init__"
                )
            )
        ):
            public_members.add(member.target.id)

    assert frozenset(public_members) == GAP_INDEX_PUBLIC_SURFACE
    assert _declared_exports(tree) == {"GapIndex"}
    assert _module_level_public_names(tree) == {"GapIndex", "annotations"}
    assert _module_owned_public_definitions(tree) == {"GapIndex"}

    module = importlib.import_module(f"{ROOT_NAME}.data.gaps")
    assert set(_runtime_public_bindings(module)) == {"GapIndex", "annotations"}
    assert isinstance(module.GapIndex, type)


def test_gap_index_declares_no_market_interpretation_or_boundary_field() -> None:
    """Arithmetic output stays free of semantic claims and OD-G2's deferred field."""
    path = PACKAGE_ROOT / "data" / "gaps.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    violations = _gaps_semantic_boundary_violations(tree)
    assert not violations, "GapIndex semantic/OD-G2 violations: " + "; ".join(violations)


def test_gap_index_has_only_the_recorded_arithmetic_capability() -> None:
    """The index consumes an issued frame and owns no I/O or access machinery."""
    path = PACKAGE_ROOT / "data" / "gaps.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    violations = _gaps_import_and_capability_violations(tree)
    assert not violations, "GapIndex capability violations: " + "; ".join(violations)


def test_gap_index_producer_is_numpy_vectorized_without_scalar_iteration() -> None:
    """The accepted diff path remains vectorized over the native-key array."""
    path = PACKAGE_ROOT / "data" / "gaps.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    violations = _gap_index_producer_violations(tree)
    assert not violations, "GapIndex producer violations: " + "; ".join(violations)


def test_gap_index_guards_detect_the_bounded_three_mutations() -> None:
    """Prove the scoped vocabulary, scalar-loop, and import guards each bite."""
    path = PACKAGE_ROOT / "data" / "gaps.py"
    source = path.read_text()

    semantic_mutation = source + "\n_session_label = None\n"
    assert _gaps_semantic_boundary_violations(ast.parse(semantic_mutation))

    loop_mutation = source.replace(
        "        keys = frame.keys\n",
        "        keys = frame.keys\n        for _key in keys:\n            pass\n",
        1,
    )
    assert loop_mutation != source
    assert _gap_index_producer_violations(ast.parse(loop_mutation))

    import_mutation = source + "\nimport pyarrow\n"
    assert _gaps_import_and_capability_violations(ast.parse(import_mutation))


# -------------------------------------------------- Slice 1F generation boundary


def test_slice_1f_generation_modules_are_internal_only() -> None:
    """Generation support has no public API or package-level re-export."""
    violations: list[str] = []
    for relative in SLICE_1F_GENERATION_MODULES:
        path = PACKAGE_ROOT / relative
        if not path.is_file():
            violations.append(f"missing required internal module {relative}")
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        exports = _declared_exports(tree)
        public_names = _module_level_public_names(tree) - {"annotations"}
        public_definitions = _module_owned_public_definitions(tree)
        if exports:
            violations.append(f"{relative} exports {sorted(exports)}")
        if public_names:
            violations.append(f"{relative} binds public names {sorted(public_names)}")
        if public_definitions:
            violations.append(f"{relative} defines public callables {sorted(public_definitions)}")

    data_init = ast.parse(
        (PACKAGE_ROOT / "data" / "__init__.py").read_text(),
        filename="data/__init__.py",
    )
    data_imports = _absolute_import_modules(data_init)
    forbidden_data_modules = {
        f"{ROOT_NAME}.data.atomic_write",
        f"{ROOT_NAME}.data.views_generation",
    }
    imported = sorted(data_imports & forbidden_data_modules)
    if imported:
        violations.append(f"data/__init__.py imports internal modules {imported}")
    forbidden_exports = {
        "GENERATION_DTYPES_BY_ADAPTER",
        "ViewGenerationError",
        "execute_prepared_bundle",
        "prepare_view_bundle",
        "publish_immutable_bytes",
    }
    leaked = sorted(_declared_exports(data_init) & forbidden_exports)
    if leaked:
        violations.append(f"data/__init__.py exports generation names {leaked}")

    manifests_init = ast.parse(
        (PACKAGE_ROOT / "data" / "manifests" / "__init__.py").read_text(),
        filename="data/manifests/__init__.py",
    )
    if f"{ROOT_NAME}.data.manifests.adapter_columns" in _absolute_import_modules(manifests_init):
        violations.append("manifests/__init__.py imports adapter dtype declarations")
    if "GENERATION_DTYPES_BY_ADAPTER" in _declared_exports(manifests_init):
        violations.append("manifests/__init__.py exports adapter dtype declarations")
    assert not violations, "Slice 1F internal-surface violations: " + "; ".join(violations)


def test_slice_1f_generation_has_one_exact_keys_only_loader_call() -> None:
    """The existing loader remains the sole key scanner and projects no values."""
    path = PACKAGE_ROOT / "data" / "views_generation.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    assert not _slice_1f_keys_only_loader_violations(tree)


def test_slice_1f_generation_has_no_decoder_or_ambient_configuration_route() -> None:
    """Generation receives authority/metadata and never reconstructs or decodes them."""
    path = PACKAGE_ROOT / "data" / "views_generation.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    violations = _slice_1f_generation_boundary_violations(tree)
    assert not violations, "Slice 1F generation boundary violations: " + "; ".join(violations)


def test_slice_1f_generation_contains_no_dataset_or_policy_assumptions() -> None:
    """Asset, timeframe, file layout, columns, and boundaries remain supplied data."""
    path = PACKAGE_ROOT / "data" / "views_generation.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    violations = _slice_1f_literal_assumption_violations(tree)
    assert not violations, "Slice 1F generation literal violations: " + "; ".join(violations)


def test_slice_1f_manifest_publication_uses_only_the_atomic_helper() -> None:
    """No final manifest filename can be exposed by a direct write/replace route."""
    path = PACKAGE_ROOT / "data" / "views_generation.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    violations = _slice_1f_publication_violations(tree)
    assert not violations, "Slice 1F publication boundary violations: " + "; ".join(violations)


def test_slice_1f_generation_guards_detect_bounded_regressions() -> None:
    """Exercise one finite mutation for each newly guarded boundary class."""
    path = PACKAGE_ROOT / "data" / "views_generation.py"
    source = path.read_text()

    value_projection = source.replace(
        "loader.load(grant, columns=())",
        'loader.load(grant, columns=("open",))',
        1,
    )
    assert value_projection != source
    assert _slice_1f_keys_only_loader_violations(ast.parse(value_projection))

    boundary_mutations = (
        "import pyarrow.parquet as _parquet\n",
        "import os as _os\n_value = _os.getenv('DATASET_ROOT')\n",
        f"from {ROOT_NAME}.features import feature as _feature\n",
        "from datetime import datetime as _datetime\n",
    )
    assert all(
        _slice_1f_generation_boundary_violations(ast.parse(mutation))
        for mutation in boundary_mutations
    )

    literal_mutations = (
        f"_CAP = {max(SLICE_1F_V1_POLICY_LITERALS)}\n",
        '_COLUMNS = ("open", "high", "low", "close")\n',
        '_ASSET = "XAUUSD.s"\n',
        "_PARTITION_COUNT = 7\n",
    )
    assert all(
        _slice_1f_literal_assumption_violations(ast.parse(mutation))
        for mutation in literal_mutations
    )

    direct_write = source.replace(
        "return _publish_immutable_bytes(path, payload)",
        "path.write_bytes(payload)\n    return True",
        1,
    )
    assert direct_write != source
    assert _slice_1f_publication_violations(ast.parse(direct_write))


# ------------------------------------------------ Slice 2H qualification boundary


def _slice_2h_performance_violations(tree: ast.Module) -> list[str]:
    """Pin the exact TRAIN-only timing topology and evidence vocabulary."""
    violations: list[str] = []
    aliases = _symbol_aliases(tree)

    expected_expressions = {
        "_AUDIT_PATH": '_RUNS_ROOT / "audit.jsonl"',
        "_EVIDENCE_SCHEMA": '"phase2-feature-performance-v1"',
        "_RUNS_ROOT": 'Path("/path/to/research-runs")',
        "_RUNS_TMP_ROOT": '_RUNS_ROOT / "tmp"',
    }
    for name, expression in expected_expressions.items():
        values = [
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign | ast.AnnAssign)
            and name in _assigned_names(node)
            and node.value is not None
        ]
        expected_expression_node = ast.parse(expression, mode="eval").body
        if len(values) != 1 or ast.dump(values[0]) != ast.dump(expected_expression_node):
            violations.append(f"feature qualification {name} path/schema binding differs")

    for name, expected_digest in SLICE_2H_FUNCTION_AST_SHA256.items():
        function = _slice_2e_function(tree, name)
        if function is None:
            violations.append(f"feature qualification function {name} is missing")
            continue
        observed_digest = hashlib.sha256(
            ast.dump(function, include_attributes=False).encode("utf-8")
        ).hexdigest()
        if observed_digest != expected_digest:
            violations.append(f"feature qualification function {name} contract differs")

    marker_values = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign | ast.AnnAssign)
        and "pytestmark" in _assigned_names(node)
        and node.value is not None
    ]
    expected_marker = ast.parse("pytest.mark.performance", mode="eval").body
    if len(marker_values) != 1 or ast.dump(marker_values[0]) != ast.dump(expected_marker):
        violations.append("feature qualification must be marked performance exactly once")

    expected_literals = {
        "_EXPECTED_AUDIT_RECORDS": 3,
        "_EXPECTED_AUDIT_SHA256": (
            "deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5"
        ),
        "_CACHE_HIT_BUDGET_SECONDS": 0.15,
        "_FULL_PASS_BUDGET_SECONDS": 5.0,
        "_REFERENCE_COLUMN_NAMES": (
            "log_return_1",
            "rolling_mean_close",
            "bar_range",
        ),
        "_REQUIRED_MARKET_COLUMNS": ("high", "low", "close"),
        "_TIMED_REPETITIONS": 5,
    }
    for name, expected_literal in expected_literals.items():
        observed = _slice_2e_literal_assignment(tree, name)
        if observed != expected_literal:
            violations.append(f"feature qualification {name} differs: {observed!r}")

    imports = _absolute_import_modules(tree)
    forbidden_import_roots = {
        "cProfile",
        "concurrent",
        "multiprocessing",
        "profile",
        "threading",
    }
    for module in sorted(imports):
        if module.split(".", maxsplit=1)[0] in forbidden_import_roots:
            violations.append(f"feature qualification imports forbidden {module}")

    forbidden_names = {
        "AccessReason",
        "GatedUnlock",
        "RAW_MANIFEST",
    }
    referenced_names = {
        name for _kind, name in _identifier_records(tree) if name in forbidden_names
    }
    violations.extend(
        f"feature qualification references forbidden authority {name}"
        for name in sorted(referenced_names)
    )
    forbidden_roles = {"CURATED", "LOCKBOX", "OOS", "RAW", "SNAPSHOT", "VALIDATION"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden_roles:
            violations.append(f"feature qualification references forbidden role {node.attr}")
        if isinstance(node, ast.Attribute) and node.attr in {"skip", "skipif", "xfail"}:
            violations.append(f"feature qualification references forbidden gate marker {node.attr}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            upper = node.value.upper()
            if any(f"XAUUSD_S_M1_{role}" in upper for role in forbidden_roles):
                violations.append("feature qualification names a non-TRAIN target")
        if isinstance(node, ast.Call):
            leaves = {
                _reference_leaf(reference)
                for reference in _expression_references(node.func, aliases)
            }
            forbidden_calls = leaves & {
                "importorskip",
                "skip",
                "skipif",
                "xfail",
            }
            violations.extend(
                f"feature qualification calls forbidden gate escape {leaf}"
                for leaf in sorted(forbidden_calls)
            )

    providers = _slice_2e_function(tree, "_reference_providers")
    expected_provider_body = ast.parse(
        "def _reference_providers():\n    return (LogReturn1(), RollingMeanClose(), BarRange())\n"
    ).body[0]
    assert isinstance(expected_provider_body, ast.FunctionDef)
    if providers is None or [ast.dump(node) for node in providers.body] != [
        ast.dump(node) for node in expected_provider_body.body
    ]:
        violations.append("reference provider declaration/order differs")

    materialize = _slice_2e_function(tree, "_materialize")
    if materialize is None:
        violations.append("missing _materialize wrapper")
    else:
        calls = [
            call
            for leaf, _line, call in _function_call_records(materialize, aliases)
            if leaf == "materialize_features"
        ]
        policies = [
            keyword.value for call in calls for keyword in call.keywords if keyword.arg == "policy"
        ]
        if (
            len(calls) != 1
            or len(policies) != 1
            or not isinstance(policies[0], ast.Attribute)
            or policies[0].attr != "PERSIST"
        ):
            violations.append("cache qualification must use one public PERSIST call")

    cross = _slice_2e_function(tree, "_measure_cross_passes")
    if cross is None:
        violations.append("missing full-pass measurement")
    else:
        nested = [
            node
            for node in cross.body
            if isinstance(node, ast.FunctionDef) and node.name == "compute_once"
        ]
        nested_leaves = (
            []
            if len(nested) != 1
            else [leaf for leaf, _line, _call in _function_call_records(nested[0], aliases)]
        )
        records = _function_call_records(cross, aliases)
        if nested_leaves != ["compute_features"]:
            violations.append("full pass must call public compute_features exactly once per run")
        if sum(leaf == "compute_once" for leaf, _line, _call in records) != 2:
            violations.append("full pass must have one discarded and one repeated call site")
        if sum(leaf == "perf_counter" for leaf, _line, _call in records) != 2:
            violations.append("full pass timer boundary differs")
        loops = [node for node in ast.walk(cross) if isinstance(node, ast.For)]
        if len(loops) != 1 or ast.dump(loops[0].iter) != ast.dump(
            ast.parse("range(_TIMED_REPETITIONS)", mode="eval").body
        ):
            violations.append("full pass must retain exactly five measured repetitions")

    generic = _slice_2e_function(tree, "_measure_feature_passes")
    if generic is None:
        violations.append("missing shared cache-hit/BREAK measurement")
    else:
        records = _function_call_records(generic, aliases)
        if sum(leaf == "operation" for leaf, _line, _call in records) != 2:
            violations.append("shared measurement must have discarded and repeated call sites")
        if sum(leaf == "perf_counter" for leaf, _line, _call in records) != 2:
            violations.append("shared measurement timer boundary differs")

    view_opener = _slice_2e_function(tree, "_open_full_train_view")
    if view_opener is None or any(isinstance(node, ast.Assert) for node in ast.walk(view_opener)):
        violations.append("timed TRAIN view opener must contain no test assertions")

    main = _slice_2e_function(
        tree,
        "test_published_train_reference_features_and_verified_cache_hit_meet_budgets",
    )
    if main is None:
        return [*violations, "missing feature qualification test"]
    main_records = _function_call_records(main, aliases)
    if any(leaf == "perf_counter" for leaf, _line, _call in main_records):
        violations.append("qualification setup must remain outside every direct timer")
    ordered_calls = (
        "_audit_snapshot",
        "_clean_committed_platform_identity",
        "_prepare_train",
        "_measure_cross_passes",
        "_measure_load_plus_full",
        "_measure_full_plus_persist",
        "ArtifactStore",
        "_ensure_canonical_cache_entry",
    )
    prior_line = -1
    for leaf in ordered_calls:
        matches = [line for observed, line, _call in main_records if observed == leaf]
        if not matches or min(matches) <= prior_line:
            violations.append(f"qualification setup/order is missing ordered {leaf}")
            break
        prior_line = min(matches)

    evidence_assignments = [
        node.value
        for node in main.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "evidence"
        and isinstance(node.value, ast.Dict)
    ]
    if len(evidence_assignments) != 1:
        violations.append("qualification must build one evidence dictionary")
    else:
        evidence = evidence_assignments[0]
        explicit_keys = {
            key.value
            for key in evidence.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        timing_prefixes = {
            value.args[0].value
            for key, value in zip(evidence.keys, evidence.values, strict=True)
            if key is None
            and isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "_timing_fields"
            and len(value.args) == 2
            and isinstance(value.args[0], ast.Constant)
            and isinstance(value.args[0].value, str)
        }
        if explicit_keys != SLICE_2H_EVIDENCE_KEYS:
            violations.append(
                "qualification evidence keys differ: "
                f"missing={sorted(SLICE_2H_EVIDENCE_KEYS - explicit_keys)}, "
                f"extra={sorted(explicit_keys - SLICE_2H_EVIDENCE_KEYS)}"
            )
        if timing_prefixes != SLICE_2H_TIMING_PREFIXES:
            violations.append(f"qualification timing evidence differs: {sorted(timing_prefixes)}")

    comparisons = {ast.dump(node) for node in ast.walk(main) if isinstance(node, ast.Compare)}
    required_comparisons = (
        "cross.timing.median_seconds < _FULL_PASS_BUDGET_SECONDS",
        "hit_timing.median_seconds < _CACHE_HIT_BUDGET_SECONDS",
        "audit_after == audit_before",
    )
    for expression in required_comparisons:
        expected_comparison = ast.dump(ast.parse(expression, mode="eval").body)
        if expected_comparison not in comparisons:
            violations.append(f"qualification is missing comparison {expression}")
    return violations


def test_slice_2h_performance_harness_is_exact_and_train_only() -> None:
    """The explicit benchmark cannot drift into gated data or vacuous timing."""
    inventory = {
        path.relative_to(FEATURE_BUDGET_TEST.parent).as_posix()
        for path in FEATURE_BUDGET_TEST.parent.rglob("*.py")
    }
    assert inventory == SLICE_2H_PERFORMANCE_INVENTORY
    tree = ast.parse(FEATURE_BUDGET_TEST.read_text(), filename=str(FEATURE_BUDGET_TEST))
    violations = _slice_2h_performance_violations(tree)
    assert not violations, "Slice 2H performance boundary violations: " + "; ".join(violations)


def test_slice_2h_performance_guards_detect_bounded_mutations() -> None:
    """Exercise each load-bearing timing, access, policy and evidence guard."""
    source = FEATURE_BUDGET_TEST.read_text()
    mutations = (
        source.replace("pytest.mark.performance", "pytest.mark.integration", 1),
        source.replace("ViewRole.TRAIN", "ViewRole.OOS", 1),
        source.replace("_TIMED_REPETITIONS = 5", "_TIMED_REPETITIONS = 4", 1),
        source.replace(
            "policy=MaterializationPolicy.PERSIST",
            "policy=MaterializationPolicy.EPHEMERAL",
            1,
        ),
        source.replace(
            "return compute_features(prepared.view, providers)",
            "return materialize_features(prepared.view, providers)",
            1,
        ),
        source.replace(
            "    prepared = _prepare_train(platform_identity)\n",
            "    perf_counter()\n    prepared = _prepare_train(platform_identity)\n",
            1,
        ),
        source.replace('        "identity_key": identity.identity_key(),\n', "", 1),
        source + "\npytest.skip('do not qualify')\n",
        source.replace(
            "def test_published_train_reference_features_and_verified_cache_hit_meet_budgets(\n",
            "@pytest.mark.xfail\n"
            "def test_published_train_reference_features_and_verified_cache_hit_meet_budgets(\n",
            1,
        ),
        source.replace(
            "        started = perf_counter()\n"
            "        frame = compute_once()\n"
            "        samples.append(perf_counter() - started)\n",
            "        frame = compute_once()\n"
            "        started = perf_counter()\n"
            "        samples.append(perf_counter() - started)\n",
            1,
        ),
        source.replace(
            "        started = perf_counter()\n"
            "        frame = operation()\n"
            "        samples.append(perf_counter() - started)\n",
            "        frame = operation()\n"
            "        started = perf_counter()\n"
            "        samples.append(perf_counter() - started)\n",
            1,
        ),
        source.replace(
            "        started = perf_counter()\n"
            "        view, frame = operation()\n"
            "        samples.append(perf_counter() - started)\n",
            "        view, frame = operation()\n"
            "        started = perf_counter()\n"
            "        samples.append(perf_counter() - started)\n",
            1,
        ),
        source.replace("            dir=_RUNS_TMP_ROOT,\n", "            dir=None,\n", 1),
        source.replace(
            '_RUNS_ROOT = Path("/path/to/research-runs")',
            '_RUNS_ROOT = Path("/tmp/not-production-runs")',
            1,
        ),
        source.replace(
            "lambda: _materialize(prepared, hit_providers, store=store),",
            "lambda: compute_features(prepared.view, hit_providers),",
            1,
        ),
        source.replace(
            '**_timing_fields("cache_hit", hit_timing),',
            '**_timing_fields("cache_hit", cross.timing),',
            1,
        ),
        source.replace(
            "    operation()\n"
            "    return _summarize([operation() for _ in range(_TIMED_REPETITIONS)])\n",
            "    return _summarize([operation() for _ in range(_TIMED_REPETITIONS)])\n",
            1,
        ),
        source.replace(
            "return _summarize([operation() for _ in range(_TIMED_REPETITIONS)])",
            "return _summarize([operation() for _ in range(1)])",
            1,
        ),
        source.replace(
            'print("PHASE2_FEATURE_EVIDENCE_BEGIN")',
            'print("PHASE2_FEATURE_EVIDENCE")',
            1,
        ),
        source.replace("_EXPECTED_AUDIT_RECORDS = 3", "_EXPECTED_AUDIT_RECORDS = 4", 1),
        source.replace(
            "deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5",
            "0" * 64,
            1,
        ),
        source.replace(
            "        started = perf_counter()\n"
            "        gap_index = compute_once()\n"
            "        samples.append(perf_counter() - started)\n",
            "        gap_index = compute_once()\n"
            "        started = perf_counter()\n"
            "        samples.append(perf_counter() - started)\n",
            1,
        ),
        source.replace(
            "assert tuple(provider.compute_calls for provider in observed) == (1, 1, 1)",
            "assert tuple(provider.compute_calls for provider in observed) == (0, 0, 0)",
            1,
        ),
        source.replace("gap_policy=GapPolicy.BREAK", "gap_policy=GapPolicy.CROSS", 1),
        source.replace(
            "assert tuple(provider.compute_calls for provider in hit_providers) == (0, 0, 0)",
            "assert tuple(provider.compute_calls for provider in hit_providers) == (1, 1, 1)",
            1,
        ),
    )
    assert all(mutation != source for mutation in mutations)
    assert all(_slice_2h_performance_violations(ast.parse(mutation)) for mutation in mutations)


# -------------------------------------------- Stage 1H-B qualification boundary


def test_snapshot_qualification_is_private_and_value_inspection_free() -> None:
    """The gated operator is non-collected and emits no market-derived evidence."""
    assert SNAPSHOT_OPERATOR_MODULE.name.startswith("_")
    tree = ast.parse(
        SNAPSHOT_OPERATOR_MODULE.read_text(),
        filename=str(SNAPSHOT_OPERATOR_MODULE),
    )
    assert not any(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test")
        for node in tree.body
    )
    violations = _snapshot_value_boundary_violations(tree)
    assert not violations, "SNAPSHOT qualification value-boundary violations: " + "; ".join(
        violations
    )


def test_phase1h_timed_load_reparses_metadata_without_reauthorization() -> None:
    """Every measured repetition owns policy read, discovery, loader, and load."""
    measure_tree = ast.parse(
        PERFORMANCE_MEASURE_CHILD.read_text(),
        filename=str(PERFORMANCE_MEASURE_CHILD),
    )
    snapshot_tree = ast.parse(
        SNAPSHOT_OPERATOR_MODULE.read_text(),
        filename=str(SNAPSHOT_OPERATOR_MODULE),
    )
    violations = _timed_load_boundary_violations(measure_tree)
    violations.extend(_snapshot_authorization_topology_violations(snapshot_tree))
    assert not violations, "Stage 1H-B timed/authorization boundary violations: " + "; ".join(
        violations
    )


def test_phase1h_guards_detect_the_bounded_value_and_authorization_mutations() -> None:
    """Exercise value-read, downstream-import, GapIndex, and timed-auth regressions."""
    snapshot_source = SNAPSHOT_OPERATOR_MODULE.read_text()
    value_read = snapshot_source + "\ndef _leak(frame):\n    return frame.column('close')\n"
    model_import = f"from {ROOT_NAME}.models import RuleModel\n" + snapshot_source
    gap_import = f"from {ROOT_NAME}.data.gaps import GapIndex\n" + snapshot_source
    import_time_load = snapshot_source + "\n_timed_load(None, columns=None)\n"
    assert _snapshot_value_boundary_violations(ast.parse(value_read))
    assert _snapshot_value_boundary_violations(ast.parse(model_import))
    assert _snapshot_value_boundary_violations(ast.parse(gap_import))
    assert _snapshot_authorization_topology_violations(ast.parse(import_time_load))

    measure_source = PERFORMANCE_MEASURE_CHILD.read_text()
    timed_authorization = measure_source.replace(
        "    started = perf_counter()\n",
        "    started = perf_counter()\n    authorize_access()\n",
        1,
    )
    assert timed_authorization != measure_source
    assert _timed_load_boundary_violations(ast.parse(timed_authorization))


# ------------------------------------------------------- metadata-only layer

# The catalog answers "what exists and what does it declare". It must stay
# free of anything that reads or decodes data, because a metadata query that
# can quietly open a partition is a gate that can be walked around.
METADATA_MODULES = (
    "registry.py",
    "gating.py",
    "audit.py",
    "manifests/models.py",
    "manifests/readers.py",
    "manifests/v11_schema.py",
    "manifests/__init__.py",
)

# Slice 1C decides authorization and records metadata, but it still cannot
# read a market row. Keeping this list narrow lets Slice 1D add its legitimate
# loader dependencies without weakening the gate/audit boundary.
SLICE_1C_MODULES = ("gating.py", "audit.py")

# Row-decoding machinery. These arrive in the data layer at Slice 1D, but must
# never reach the metadata modules above.
ROW_DECODING_LIBRARIES = {"pyarrow", "numpy", "pandas", "polars", "duckdb"}


def _metadata_module_paths() -> list[Path]:
    return [PACKAGE_ROOT / "data" / name for name in METADATA_MODULES]


def _slice_1c_module_paths() -> list[Path]:
    return [PACKAGE_ROOT / "data" / name for name in SLICE_1C_MODULES]


def test_metadata_layer_decodes_no_rows() -> None:
    """The catalog must not be able to read data, only describe it."""
    violations: list[str] = []
    for path in _metadata_module_paths():
        tree = ast.parse(path.read_text(), filename=str(path))
        forbidden = _imported_module_roots(tree) & ROW_DECODING_LIBRARIES
        violations.extend(
            f"{path.relative_to(PACKAGE_ROOT)} -> {module}" for module in sorted(forbidden)
        )
    assert not violations, "metadata modules must not import row decoders: " + "; ".join(violations)


def test_slice_1c_imports_no_loader_or_row_reader() -> None:
    """A grant can exist before Slice 1D, but no code here can consume rows."""
    banned_modules = {
        f"{ROOT_NAME}.core.frames",
        f"{ROOT_NAME}.data.integrity",
        f"{ROOT_NAME}.data.adapters",
    }
    violations: list[str] = []
    for path in _slice_1c_module_paths():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            for module in modules:
                if any(
                    module == banned or module.startswith(f"{banned}.") for banned in banned_modules
                ):
                    violations.append(f"{path.relative_to(PACKAGE_ROOT)} -> {module}")
        if "MarketFrame" in path.read_text():
            violations.append(f"{path.relative_to(PACKAGE_ROOT)} names MarketFrame")
    assert not violations, "Slice 1C must remain upstream of every row reader: " + "; ".join(
        violations
    )


def test_gating_imports_no_civil_time_machinery() -> None:
    """Market-key classification is integer-only; operational UTC lives in audit."""
    path = PACKAGE_ROOT / "data" / "gating.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    forbidden = _imported_module_roots(tree) & CIVIL_TIME_MODULES
    assert not forbidden, (
        f"data/gating.py must not import civil-time machinery: {sorted(forbidden)}"
    )


def test_gating_does_not_hardcode_the_current_oos_boundary() -> None:
    """The boundary comes from exact policy bytes, so code cannot carry a copy."""
    policy_path = PACKAGE_ROOT.parents[1] / "config" / "split-policies" / "adr-0012-v1.json"
    policy = json.loads(policy_path.read_bytes())
    boundary = policy["ranges"]["oos"]["end_inclusive_key"]
    tree = ast.parse((PACKAGE_ROOT / "data" / "gating.py").read_text())
    constants = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, int)
    }
    assert boundary not in constants, (
        "data/gating.py hardcodes the configured OOS boundary instead of reading policy"
    )


def test_gating_never_reads_a_manifest_gated_field() -> None:
    """Authorization is derived from policy and range; manifests make no claim."""
    path = PACKAGE_ROOT / "data" / "gating.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[int] = []
    for node in ast.walk(tree):
        attribute_access = isinstance(node, ast.Attribute) and node.attr == "gated"
        subscript_access = (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "gated"
        )
        if attribute_access or subscript_access:
            violations.append(getattr(node, "lineno", -1))
    assert not violations, f"data/gating.py reads a manifest 'gated' field at {violations}"


def test_the_data_layer_never_discovers_partition_files_by_directory_scan() -> None:
    """Manifest discovery by exact name is legal; scanning for data is not.

    A directory listing that decides which partitions are admitted would
    silently re-admit data a curated manifest deliberately excluded, so the
    ban is on the pattern, not on globbing itself — searching for the manifest
    filename stays legal and is how the catalog finds anything at all.

    The sole allowed scan is glob/rglob for the exact manifest filename.  Data
    discovery through another pattern or through directory enumeration is a
    second admission authority and is refused even when followed by filtering.
    """
    from trading_research.data.manifests import MANIFEST_FILENAME

    approved: dict[str, str] = {"MANIFEST_FILENAME": MANIFEST_FILENAME}
    assert MANIFEST_FILENAME == "manifest.json", (
        "the approved discovery constant changed; re-check this guard before widening it"
    )

    violations: list[str] = []
    for path in _layer_files("data"):
        tree = ast.parse(path.read_text(), filename=str(path))
        aliases = _symbol_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            call_names = {
                _reference_leaf(reference) for reference in _expression_references(func, aliases)
            }
            enumerators = call_names & {"iterdir", "listdir", "scandir", "walk"}
            if enumerators:
                violations.append(
                    f"{path.relative_to(PACKAGE_ROOT)}: directory enumeration via "
                    f"{sorted(enumerators)}"
                )
                continue
            globbers = call_names & {"glob", "rglob"}
            if not globbers:
                continue
            if len(node.args) != 1 or node.keywords:
                violations.append(
                    f"{path.relative_to(PACKAGE_ROOT)}: {sorted(globbers)} with an "
                    "unapproved call shape"
                )
                continue
            argument = node.args[0]
            pattern: str | None = None
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                pattern = argument.value
            elif isinstance(argument, ast.Name):
                pattern = approved.get(argument.id)
            if pattern != MANIFEST_FILENAME:
                violations.append(
                    f"{path.relative_to(PACKAGE_ROOT)}: {sorted(globbers)} with "
                    f"unapproved partition-discovery pattern {pattern!r}"
                )
    assert not violations, "the data layer scans directories for partitions: " + "; ".join(
        violations
    )


def test_metadata_layer_does_not_import_legacy_bulk_carriers() -> None:
    """The catalog is built on the Phase 1 contracts, not the carriers they replace."""
    violations: list[str] = []
    for path in _metadata_module_paths():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                violations.extend(
                    f"{path.relative_to(PACKAGE_ROOT)} imports {a.name}"
                    for a in node.names
                    if a.name in LEGACY_ROW_CARRIERS
                )
    assert not violations, "metadata modules must not import legacy carriers: " + "; ".join(
        violations
    )


def test_native_primitives_do_not_import_legacy_bulk_carriers() -> None:
    """New Phase 1 contracts must not be built on the carriers they replace."""
    violations: list[str] = []
    for path in _primitive_module_paths():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                violations.extend(
                    f"{path.relative_to(PACKAGE_ROOT)} imports {a.name}"
                    for a in node.names
                    if a.name in LEGACY_ROW_CARRIERS
                )
    assert not violations, "Phase 1 primitives must not import legacy carriers: " + "; ".join(
        violations
    )


# --------------------------------------------------------- Phase 5 experiment operations


def test_phase5_layers_and_private_module_inventories_are_exact() -> None:
    """Phase 5 adds one derived read layer and one composition layer only."""
    assert {
        path.relative_to(PACKAGE_ROOT / "index").as_posix()
        for path in (PACKAGE_ROOT / "index").rglob("*.py")
    } == {"__init__.py", "build.py", "query.py", "schema.py", "trials.py"}
    assert {
        path.relative_to(PACKAGE_ROOT / "ops").as_posix()
        for path in (PACKAGE_ROOT / "ops").rglob("*.py")
    } == {
        "__init__.py",
        "cli.py",
        "phase7_validation.py",
        "phase8_entry_validation.py",
        "phase8_joint_validation.py",
        "phase8_tp_validation.py",
    }

    for layer, relative_paths in {
        "index": {"__init__.py", "build.py", "query.py", "schema.py", "trials.py"},
        "experiments": {"pool.py", "resources.py", "sweeps.py"},
    }.items():
        for relative in relative_paths:
            path = PACKAGE_ROOT / layer / relative
            tree = ast.parse(path.read_text(), filename=str(path))
            assert not _declared_exports(tree)
            assert _module_level_public_names(tree) <= {"annotations"}

    ops_init = ast.parse((PACKAGE_ROOT / "ops" / "__init__.py").read_text())
    assert not _declared_exports(ops_init)
    assert _module_level_public_names(ops_init) == {"annotations"}
    ops_cli = ast.parse((PACKAGE_ROOT / "ops" / "cli.py").read_text())
    assert _declared_exports(ops_cli) == {"main"}
    assert _module_level_public_names(ops_cli) == {"annotations", "main"}


def test_phase5_index_dependency_and_sqlite_direction_are_one_way() -> None:
    """Experiment truth never reaches into its disposable SQLite projection."""
    experiment_violations = [
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in _layer_files("experiments")
        if "index" in _internal_imports(path)
    ]
    assert not experiment_violations

    sqlite_violations: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        imported_roots = _imported_module_roots(ast.parse(path.read_text(), filename=str(path)))
        if "sqlite3" in imported_roots and path.parent != PACKAGE_ROOT / "index":
            sqlite_violations.append(path.relative_to(PACKAGE_ROOT).as_posix())
    assert not sqlite_violations, "SQLite escaped the disposable index: " + "; ".join(
        sqlite_violations
    )


def test_phase5_pool_is_the_only_possible_nested_parallelism_location() -> None:
    """Whole-run dispatch is centralized; workers cannot recursively create pools."""
    violations: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        modules = _absolute_import_modules(ast.parse(path.read_text(), filename=str(path)))
        parallel = {
            module
            for module in modules
            if module == "multiprocessing" or module.startswith("concurrent.futures")
        }
        if parallel and path != PACKAGE_ROOT / "experiments" / "pool.py":
            violations.append(f"{path.relative_to(PACKAGE_ROOT)} imports {sorted(parallel)}")
    assert not violations, "nested parallelism escaped pool.py: " + "; ".join(violations)
    worker_tree = ast.parse((PACKAGE_ROOT / "experiments" / "worker.py").read_text())
    assert f"{ROOT_NAME}.experiments.pool" not in _absolute_import_modules(worker_tree)


def test_phase5_operations_surface_has_no_delete_or_raw_sql_verb() -> None:
    """Operators receive fixed typed verbs, never retention or a query language."""
    path = PACKAGE_ROOT / "ops" / "cli.py"
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    verbs = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_parser"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert verbs == {
        "compare",
        "index",
        "plan",
        "query",
        "rebuild",
        "report",
        "run",
        "runs",
        "status",
        "sweep",
        "trials",
    }
    forbidden_verbs = {"archive", "delete", "gc", "prune", "purge", "retain", "sql"}
    assert not verbs & forbidden_verbs
    assert "--sql" not in source
    assert "sqlite3" not in source
    assert "SELECT " not in source.upper()


def test_phase5_pool_commands_only_the_existing_worker_and_clamps_every_child() -> None:
    """No fold task or nested executor can be dispatched by the run pool."""
    pool_path = PACKAGE_ROOT / "experiments" / "pool.py"
    pool_source = pool_path.read_text()
    pool_tree = ast.parse(pool_source, filename=str(pool_path))
    command = next(
        node
        for node in pool_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_worker_command"
    )
    literals = {
        node.value
        for node in ast.walk(command)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "trading_research.experiments.worker" in literals
    assert not {"fold", "fold-worker", "fold_worker"} & literals
    references = {
        _reference_leaf(reference)
        for reference in _expression_references(pool_tree, _symbol_aliases(pool_tree))
    }
    assert "_worker_environment" in references
    assert "ProcessPoolExecutor" not in pool_source


def test_phase5_run_records_have_no_sweep_back_reference() -> None:
    """Sweep membership remains an external fingerprint list, never run truth."""
    for relative in ("config.py", "run_identity.py", "runs.py", "worker.py"):
        source = (PACKAGE_ROOT / "experiments" / relative).read_text()
        assert "sweep_id" not in source
        assert "child_ordinal" not in source
