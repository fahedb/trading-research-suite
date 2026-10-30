"""Fixed-stage Slice-3I orchestration, resume, and run-level verification."""

from __future__ import annotations

import getpass as _getpass
import hashlib as _hashlib
import inspect as _inspect
import io as _io
import json as _json
import os as _os
import platform as _platform
import re as _re
import socket as _socket
import subprocess as _subprocess
import threading as _threading
import time as _time
import typing as _typing
from collections.abc import Mapping as _Mapping
from collections.abc import Sequence as _Sequence
from dataclasses import dataclass as _dataclass
from itertools import pairwise as _pairwise
from pathlib import Path as _Path
from uuid import uuid4 as _uuid4

import numpy as _np

from trading_research.artifacts import (
    ArtifactStore as _ArtifactStore,
)
from trading_research.artifacts import (
    MaterializationPolicy as _MaterializationPolicy,
)
from trading_research.artifacts import (
    PlatformIdentity as _PlatformIdentity,
)
from trading_research.artifacts import (
    verify_determinism as _verify_feature_determinism,
)
from trading_research.artifacts.container import _validate_zip_envelope
from trading_research.artifacts.identity import _canonical_identity_json
from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.errors import TradingResearchError as _TradingResearchError
from trading_research.core.feature_frames import FeatureFrame as _FeatureFrame
from trading_research.core.feature_frames import _restore_feature_frame
from trading_research.core.features import GapPolicy as _GapPolicy
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.features import canonical_params_json as _canonical_params_json
from trading_research.core.research import BoundaryPolicy as _BoundaryPolicy
from trading_research.core.research import ModelInputs as _ModelInputs
from trading_research.core.research import StageOrigin as _StageOrigin
from trading_research.core.research import ToleranceSpec as _ToleranceSpec
from trading_research.core.research import ViewSpan as _ViewSpan
from trading_research.core.research_frames import FittedState as _FittedState
from trading_research.core.research_frames import IntentFrame as _IntentFrame
from trading_research.core.research_frames import ModelOutputFrame as _ModelOutputFrame
from trading_research.core.research_frames import OutcomeFrame as _OutcomeFrame
from trading_research.core.research_frames import RepresentationFrame as _RepresentationFrame
from trading_research.core.research_frames import TrainingTargetFrame as _TrainingTargetFrame
from trading_research.core.research_frames import _make_training_target_frame
from trading_research.core.simulation import EquityCurve as _EquityCurve
from trading_research.core.simulation import ExecutionSpec as _ExecutionSpec
from trading_research.core.simulation import SimulationRequest as _SimulationRequest
from trading_research.core.simulation import TradeExitReason as _TradeExitReason
from trading_research.core.simulation import TradeLog as _TradeLog
from trading_research.core.simulation import (
    _execution_spec_params_json,
    _make_simulation_request,
    _trade_exit_reason_code,
)
from trading_research.core.time import NativeTimeKey as _NativeTimeKey
from trading_research.data import (
    AccessClassification as _AccessClassification,
)
from trading_research.data import (
    AccessContext as _AccessContext,
)
from trading_research.data import (
    AccessRequest as _AccessRequest,
)
from trading_research.data import (
    DatasetRegistry as _DatasetRegistry,
)
from trading_research.data import (
    GatedUnlock as _GatedUnlock,
)
from trading_research.data import (
    JsonlAuditWriter as _JsonlAuditWriter,
)
from trading_research.data import (
    MarketFrameLoader as _MarketFrameLoader,
)
from trading_research.data import (
    authorize_access as _authorize_access,
)
from trading_research.data import (
    classify_access as _classify_access,
)
from trading_research.data import (
    open_bounded_view as _open_bounded_view,
)
from trading_research.data import (
    read_split_policy as _read_split_policy,
)
from trading_research.decisions import DECISION_POLICIES as _DECISION_POLICIES
from trading_research.decisions.execution import _snapshot_policy
from trading_research.evaluation import EVALUATORS as _EVALUATORS
from trading_research.evaluation import TRADE_EVALUATORS as _TRADE_EVALUATORS
from trading_research.experiments.config import (
    ResolvedExperimentConfig as _ResolvedExperimentConfig,
)
from trading_research.experiments.config import (
    _ComponentSelection,
    _config_from_document,
)
from trading_research.experiments.decisions import _infer_once_and_fan_out
from trading_research.experiments.evaluation import (
    _descriptive_inventory,
    _evaluate_plane_a_inventory,
    _evaluate_plane_b_inventory,
    _evaluation_inventory_document,
    _EvaluationInventory,
    _FittedOutput,
    _OutcomeTarget,
    _PolicyExecutionRecord,
    _preflight_plane_a,
    _preflight_plane_b,
    _record_policy_executions,
    _SimulatedPolicyResult,
)
from trading_research.experiments.features import (
    _feature_stage_identity,
)
from trading_research.experiments.features import (
    materialize_features as _materialize_features,
)
from trading_research.experiments.fitting import (
    _infer_corpus_model,
)
from trading_research.experiments.fitting import (
    encode_representation as _encode_representation,
)
from trading_research.experiments.fitting import (
    fit_model as _fit_model,
)
from trading_research.experiments.fitting import (
    fit_representation as _fit_representation,
)
from trading_research.experiments.fitting import (
    infer_model as _infer_model,
)
from trading_research.experiments.fitting import (
    view_span_from_bounded_view as _view_span_from_bounded_view,
)
from trading_research.experiments.folds import (
    _FoldPlan,
    _generate_walk_forward_folds,
    _KeyBounds,
    _PurgeOverride,
)
from trading_research.experiments.outcomes import (
    compute_outcomes as _compute_outcomes,
)
from trading_research.experiments.outcomes import (
    compute_training_targets as _compute_training_targets,
)
from trading_research.experiments.run_identity import (
    _ComponentSource,
    _ExperimentIdentity,
)
from trading_research.experiments.runs import (
    _RUN_FILE_LIMIT_BYTES,
    _finish_run,
    _heartbeat_run,
    _read_registered_config,
    _read_registered_identity,
    _record_determinism_report,
    _record_evaluation_inventory,
    _record_run_access,
    _record_run_access_attempt,
    _record_run_payload,
    _register_new_run,
    _start_run,
)
from trading_research.experiments.runs import (
    RunManifest as _RunManifest,
)
from trading_research.experiments.runs import (
    RunState as _RunState,
)
from trading_research.experiments.training_bundles import (
    _FrozenReference,
    _load_frozen_state,
)
from trading_research.features import FEATURE_PROVIDERS as _FEATURE_PROVIDERS
from trading_research.features.execution import _validated_plan as _validated_feature_plan
from trading_research.models import CORPUS_RESEARCH_MODELS as _CORPUS_RESEARCH_MODELS
from trading_research.models import RESEARCH_MODELS as _RESEARCH_MODELS
from trading_research.models.execution import _snapshot_corpus_model, _snapshot_model
from trading_research.outcomes import OUTCOME_SPECS as _OUTCOME_SPECS
from trading_research.outcomes.execution import _validated_spec
from trading_research.representations import (
    REPRESENTATION_MODELS as _REPRESENTATION_MODELS,
)
from trading_research.representations.execution import _snapshot_representation
from trading_research.simulation import (
    simulate_bracket_non_interruptible_v1 as _simulate_non_interruptible,
)
from trading_research.simulation import (
    simulate_bracket_single_position_v1 as _simulate_interruptible,
)

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _TypeMapping
    from typing import Protocol as _Protocol

    import numpy.typing as _npt

    from trading_research.artifacts import DeterminismReport as _FeatureReport
    from trading_research.core.frames import MarketFrame as _MarketFrame
    from trading_research.core.identity import DatasetIdentity as _DatasetIdentity
    from trading_research.core.interfaces import CorpusResearchModel as _CorpusResearchModel
    from trading_research.core.interfaces import DecisionPolicy as _DecisionPolicy
    from trading_research.core.interfaces import Evaluator as _Evaluator
    from trading_research.core.interfaces import FeatureProvider as _FeatureProvider
    from trading_research.core.interfaces import OutcomeSpec as _OutcomeSpec
    from trading_research.core.interfaces import RepresentationModel as _RepresentationModel
    from trading_research.core.interfaces import ResearchModel as _ResearchModel
    from trading_research.core.interfaces import TradeEvaluator as _TradeEvaluator
    from trading_research.data.gating import AccessGrant as _AccessGrant
    from trading_research.data.gating import SplitPolicy as _SplitPolicy
    class _VersionedComponent(_Protocol):
        @property
        def version(self) -> int: ...

        @property
        def params(self) -> _TypeMapping[str, _ParamValue]: ...

    class _AuditWriter(_Protocol):
        def append(self, event: _TypeMapping[str, object]) -> None: ...


__all__ = ["run_experiment", "verify_experiment"]

_SHA256 = _re.compile(r"^[0-9a-f]{64}$")
_REPORT_SCHEMA = "determinism-report-v1"
_ARTIFACT_FORMAT_VERSION = 1
_RUN_FRAME_EXPANDED_LIMIT_BYTES = 536_870_912  # 512 MiB
# The TradeLog container alone carries a second version: Slice B added the two
# recorded bracket-level columns, and unlike the intent container it has no
# schema id of its own with which a reader could discriminate. Every other
# artifact format is unchanged, so no other reader is disturbed.
_TRADE_LOG_FORMAT_VERSION = 2
_HEARTBEAT_INTERVAL_SECONDS = 5.0


class _OrchestrationError(_TradingResearchError):
    """A resolved experiment cannot execute through the fixed V1 pipeline."""


@_dataclass(frozen=True, slots=True)
class _RuntimeEnvironment:
    _runs_root: _Path
    _registry: _DatasetRegistry
    _split_policy: _SplitPolicy
    _loader: _MarketFrameLoader
    _store: _ArtifactStore
    _audit_writer: _AuditWriter
    _platform_identity: _PlatformIdentity
    _dependency_lock_sha256: str
    _machine_identity: _TypeMapping[str, object]
    _git_diff: str | None
    _lockfile_in_sync: bool
    _full_integrity_verified: bool

    def __post_init__(self) -> None:
        if not isinstance(self._runs_root, _Path) or not self._runs_root.is_absolute():
            raise _ContractViolationError("runtime runs_root must be an absolute Path")
        if type(self._registry) is not _DatasetRegistry:
            raise _ContractViolationError("runtime registry must be exact")
        if type(self._loader) is not _MarketFrameLoader:
            raise _ContractViolationError("runtime loader must be exact")
        if type(self._store) is not _ArtifactStore:
            raise _ContractViolationError("runtime store must be exact")
        if type(self._platform_identity) is not _PlatformIdentity:
            raise _ContractViolationError("runtime platform identity must be exact")
        if (
            type(self._dependency_lock_sha256) is not str
            or _SHA256.fullmatch(self._dependency_lock_sha256) is None
        ):
            raise _ContractViolationError("runtime dependency lock must be SHA-256")
        if not isinstance(self._machine_identity, _Mapping) or not self._machine_identity:
            raise _ContractViolationError("runtime machine identity must be non-empty")
        for value, label in (
            (self._lockfile_in_sync, "lockfile_in_sync"),
            (self._full_integrity_verified, "full_integrity_verified"),
        ):
            if type(value) is not bool:
                raise _ContractViolationError(f"runtime {label} must be exact bool")
        if not callable(getattr(self._audit_writer, "append", None)):
            raise _ContractViolationError("runtime audit writer must append events")


@_dataclass(frozen=True, slots=True)
class _PreparedExperiment:
    _config: _ResolvedExperimentConfig
    _identity: _ExperimentIdentity
    _feature_providers: tuple[_FeatureProvider, ...]
    _representation: _RepresentationModel | None
    _model: _ResearchModel | _CorpusResearchModel
    _training_outcome: _OutcomeSpec | None
    _evaluation_outcomes: tuple[_OutcomeSpec, ...]
    _policies: tuple[_DecisionPolicy, ...]
    _evaluators: tuple[_Evaluator, ...]
    _execution: _ExecutionSpec | None
    _trade_evaluators: tuple[_TradeEvaluator, ...]
    _model_output_names: tuple[str, ...]


@_dataclass(frozen=True, slots=True)
class _FrozenEvaluationPlan:
    """One evaluation-only span with no fabricated fit geometry."""

    _index: int
    _eval_position_range: tuple[int, int]
    _eval_key_bounds: _KeyBounds
    _evaluation_view: _ViewSpan


@_dataclass(frozen=True, slots=True)
class _ComparableArtifact:
    _label: str
    _structure_json: str
    _exact_arrays: tuple[_npt.NDArray[_typing.Any], ...]
    _floating_arrays: tuple[_npt.NDArray[_typing.Any], ...]
    _tolerance: _ToleranceSpec | None


@_dataclass(frozen=True, slots=True)
class _PipelineProducts:
    _inventory: _EvaluationInventory
    _comparables: tuple[_ComparableArtifact, ...]
    _feature_report: _FeatureReport | None


@_dataclass(frozen=True, slots=True)
class _VerificationResult:
    _first_run_id: str
    _second_run_id: str
    _passed: bool
    _report: _TypeMapping[str, object]
    _first_manifest: _RunManifest
    _second_manifest: _RunManifest


def _runtime_from_paths(
    *,
    config: _ResolvedExperimentConfig,
    data_roots: _Sequence[_Path],
    runs_root: _Path,
    split_policy_path: _Path,
    audit_path: _Path,
    repository_root: _Path,
    dependency_lock_path: _Path,
) -> _RuntimeEnvironment:
    """Resolve explicit machine locations without putting paths in identity."""
    if type(config) is not _ResolvedExperimentConfig:
        raise _ContractViolationError("config must be exact")
    roots = tuple(data_roots)
    if not roots or any(not isinstance(root, _Path) for root in roots):
        raise _ContractViolationError("data_roots must be a non-empty Path sequence")
    for path, label in (
        (runs_root, "runs_root"),
        (split_policy_path, "split_policy_path"),
        (audit_path, "audit_path"),
        (repository_root, "repository_root"),
        (dependency_lock_path, "dependency_lock_path"),
    ):
        if not isinstance(path, _Path) or not path.is_absolute():
            raise _ContractViolationError(f"{label} must be an absolute Path")
    if (
        runs_root == repository_root
        or repository_root in runs_root.parents
        or runs_root in repository_root.parents
    ):
        raise _ContractViolationError(
            "runs_root and repository_root must be separate directory trees"
        )
    try:
        runs_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _ContractViolationError(f"cannot create runs_root {runs_root}") from exc
    registry = _DatasetRegistry.discover(roots)
    policy = _read_split_policy(
        split_policy_path,
        expected_identity=config._split_policy_identity,
    )
    platform_identity, git_diff = _platform_evidence(repository_root)
    dependency_digest = _sha256_file(dependency_lock_path)
    lockfile_in_sync = _lockfile_is_in_sync(repository_root)
    return _RuntimeEnvironment(
        _runs_root=runs_root,
        _registry=registry,
        _split_policy=policy,
        _loader=_MarketFrameLoader(registry=registry, split_policy=policy),
        _store=_ArtifactStore(runs_root),
        _audit_writer=_JsonlAuditWriter(audit_path),
        _platform_identity=platform_identity,
        _dependency_lock_sha256=dependency_digest,
        _machine_identity={
            "hostname": _socket.gethostname(),
            "machine": _platform.machine(),
            "python": _platform.python_version(),
            "system": _platform.system(),
        },
        _git_diff=git_diff,
        _lockfile_in_sync=lockfile_in_sync,
        _full_integrity_verified=True,
    )


def _prepare_experiment(
    config: _ResolvedExperimentConfig,
    runtime: _RuntimeEnvironment,
) -> _PreparedExperiment:
    if type(config) is not _ResolvedExperimentConfig:
        raise _ContractViolationError("config must be exact")
    if type(runtime) is not _RuntimeEnvironment:
        raise _ContractViolationError("runtime must be exact")
    runtime._registry.resolve(config._dataset_ref)
    if runtime._split_policy.identity != config._split_policy_identity:
        raise _ContractViolationError("runtime split policy differs from resolved config")

    providers = _typing.cast(
        "tuple[_FeatureProvider, ...]",
        tuple(
            _create_component(selection, kind="feature_provider") for selection in config._features
        ),
    )
    representation = _typing.cast(
        "_RepresentationModel | None",
        (
            None
            if config._representation is None
            else _create_component(config._representation, kind="representation_model")
        ),
    )
    model_kind = "research_model" if config._frozen_state is None else "corpus_research_model"
    model = _typing.cast(
        "_ResearchModel | _CorpusResearchModel",
        _create_component(config._model, kind=model_kind),
    )
    training_outcome = _typing.cast(
        "_OutcomeSpec | None",
        (
            None
            if config._training_outcome is None
            else _create_component(config._training_outcome, kind="outcome_spec")
        ),
    )
    evaluation_outcomes = _typing.cast(
        "tuple[_OutcomeSpec, ...]",
        tuple(
            _create_component(selection, kind="outcome_spec")
            for selection in config._evaluation_outcomes
        ),
    )
    policies = _typing.cast(
        "tuple[_DecisionPolicy, ...]",
        tuple(
            _create_component(selection, kind="decision_policy") for selection in config._policies
        ),
    )
    evaluators = _typing.cast(
        "tuple[_Evaluator, ...]",
        tuple(_create_component(selection, kind="evaluator") for selection in config._evaluators),
    )
    trade_evaluators = _typing.cast(
        "tuple[_TradeEvaluator, ...]",
        tuple(
            _create_component(selection, kind="trade_evaluator")
            for selection in config._trade_evaluators
        ),
    )
    output_names = _preflight_components(
        providers=providers,
        representation=representation,
        model=model,
        training_outcome=training_outcome,
        evaluation_outcomes=evaluation_outcomes,
        policies=policies,
        evaluators=evaluators,
        execution=config._execution,
        trade_evaluators=trade_evaluators,
        corpus_model=config._frozen_state is not None,
    )
    components = _component_sources(
        providers=providers,
        representation=representation,
        model=model,
        training_outcome=training_outcome,
        evaluation_outcomes=evaluation_outcomes,
        policies=policies,
        evaluators=evaluators,
        trade_evaluators=trade_evaluators,
        corpus_model=config._frozen_state is not None,
    )
    identity = _ExperimentIdentity(
        resolved_config=config.document(),
        dataset_identities=(config._dataset_ref.canonical_identity,),
        components=components,
        platform_identity=runtime._platform_identity,
        dependency_lock_sha256=runtime._dependency_lock_sha256,
        seed=config._seed,
        machine_identity=runtime._machine_identity,
        git_diff=runtime._git_diff,
        lockfile_in_sync=runtime._lockfile_in_sync,
        full_integrity_verified=runtime._full_integrity_verified,
        declared_lockbox_evaluation=config._declared_lockbox_evaluation,
    )
    return _PreparedExperiment(
        config,
        identity,
        providers,
        representation,
        model,
        training_outcome,
        evaluation_outcomes,
        policies,
        evaluators,
        config._execution,
        trade_evaluators,
        output_names,
    )


def _create_component(selection: _ComponentSelection, *, kind: str) -> object:
    params = dict(selection._params)
    if kind == "feature_provider" and "gap_policy" in params:
        try:
            params["gap_policy"] = _GapPolicy(_typing.cast("str", params["gap_policy"]))
        except (TypeError, ValueError) as exc:
            raise _ContractViolationError("feature gap_policy is unsupported") from exc
    if kind == "outcome_spec" and "boundary_policy" in params:
        try:
            params["boundary_policy"] = _BoundaryPolicy(
                _typing.cast("str", params["boundary_policy"])
            )
        except (TypeError, ValueError) as exc:
            raise _ContractViolationError("outcome boundary_policy is unsupported") from exc
    component: object
    if kind == "decision_policy":
        component = _DECISION_POLICIES.create(selection._name, **params)
    elif kind == "evaluator":
        component = _EVALUATORS.create(selection._name, **params)
    elif kind == "trade_evaluator":
        component = _TRADE_EVALUATORS.create(selection._name, **params)
    elif kind == "feature_provider":
        component = _FEATURE_PROVIDERS.create(selection._name, **params)
    elif kind == "outcome_spec":
        component = _OUTCOME_SPECS.create(selection._name, **params)
    elif kind == "representation_model":
        component = _REPRESENTATION_MODELS.create(selection._name, **params)
    elif kind == "research_model":
        component = _RESEARCH_MODELS.create(selection._name, **params)
    elif kind == "corpus_research_model":
        component = _CORPUS_RESEARCH_MODELS.create(selection._name, **params)
    else:  # pragma: no cover - callers use the closed kind list
        raise AssertionError(f"unknown component kind {kind!r}")
    identifier_name = {
        "decision_policy": "policy_id",
        "evaluator": "evaluator_id",
        "trade_evaluator": "evaluator_id",
        "feature_provider": "provider_id",
        "outcome_spec": "outcome_id",
        "representation_model": "representation_id",
        "research_model": "model_id",
        "corpus_research_model": "model_id",
    }[kind]
    if (
        getattr(component, identifier_name, None) != selection._name
        or getattr(component, "version", None) != selection._version
    ):
        raise _ContractViolationError(
            f"resolved {kind} identity differs from its registry implementation"
        )
    actual_params = getattr(component, "params", None)
    if not isinstance(actual_params, _Mapping) or _canonical_params_json(
        actual_params
    ) != _canonical_params_json(selection._params):
        raise _ContractViolationError(
            f"resolved {kind} params differ from its registry implementation"
        )
    return component


def _preflight_components(
    *,
    providers: tuple[_FeatureProvider, ...],
    representation: _RepresentationModel | None,
    model: _ResearchModel | _CorpusResearchModel,
    training_outcome: _OutcomeSpec | None,
    evaluation_outcomes: tuple[_OutcomeSpec, ...],
    policies: tuple[_DecisionPolicy, ...],
    evaluators: tuple[_Evaluator, ...],
    execution: _ExecutionSpec | None,
    trade_evaluators: tuple[_TradeEvaluator, ...],
    corpus_model: bool = False,
) -> tuple[str, ...]:
    feature_plans = _validated_feature_plan(providers)
    feature_names = tuple(name for plan in feature_plans for name in plan._feature_names)
    if len(set(feature_names)) != len(feature_names):
        raise _ContractViolationError("resolved features emit duplicate columns")
    model_plan = _snapshot_corpus_model(model) if corpus_model else _snapshot_model(model)
    missing_features = tuple(
        name for name in model_plan._required_feature_names if name not in feature_names
    )
    if missing_features:
        raise _ContractViolationError(
            f"model requires unavailable feature columns {missing_features!r}"
        )
    if representation is None:
        if model_plan._required_representation_names:
            raise _ContractViolationError(
                "model requires a representation but config declares none"
            )
    else:
        representation_plan = _snapshot_representation(representation)
        missing_representation_features = tuple(
            name
            for name in representation_plan._required_feature_names
            if name not in feature_names
        )
        if missing_representation_features:
            raise _ContractViolationError(
                "representation requires unavailable feature columns "
                f"{missing_representation_features!r}"
            )
        if not model_plan._required_representation_names:
            raise _ContractViolationError(
                "configured representation is unused by the selected model"
            )
        if any(
            name not in representation_plan._output_column_names
            for name in model_plan._required_representation_names
        ):
            raise _ContractViolationError("model representation requirements are unavailable")
    if training_outcome is not None:
        _validated_spec(training_outcome)
    target_names: list[str] = []
    for outcome in evaluation_outcomes:
        outcome_plan = _validated_spec(outcome)
        for name in outcome_plan.target_column_names:
            if name not in target_names:
                target_names.append(name)
    for policy in policies:
        policy_plan = _snapshot_policy(policy)
        missing = tuple(
            name
            for name in policy_plan._required_output_columns
            if name not in model_plan._output_column_names
        )
        if missing:
            raise _ContractViolationError(
                f"decision policy requires unavailable model columns {missing!r}"
            )
    if evaluators:
        _preflight_plane_a(
            output_column_names=model_plan._output_column_names,
            outcome_column_names=tuple(target_names),
            evaluators=evaluators,
        )
    elif evaluation_outcomes:
        raise AssertionError("config parsing admitted outcomes without evaluators")
    if execution is None:
        if trade_evaluators:
            raise _ContractViolationError("trade evaluators require execution")
    else:
        if type(execution) is not _ExecutionSpec:
            raise _ContractViolationError("execution must be an exact ExecutionSpec")
        if not policies:
            raise _ContractViolationError("simulation requires a DecisionPolicy")
        if trade_evaluators:
            _preflight_plane_b(trade_evaluators)
    return model_plan._output_column_names


def _component_sources(
    *,
    providers: tuple[_FeatureProvider, ...],
    representation: _RepresentationModel | None,
    model: _ResearchModel | _CorpusResearchModel,
    training_outcome: _OutcomeSpec | None,
    evaluation_outcomes: tuple[_OutcomeSpec, ...],
    policies: tuple[_DecisionPolicy, ...],
    evaluators: tuple[_Evaluator, ...],
    trade_evaluators: tuple[_TradeEvaluator, ...],
    corpus_model: bool = False,
) -> tuple[_ComponentSource, ...]:
    ordered: list[tuple[str, object, str]] = []
    ordered.extend(("feature_provider", item, "provider_id") for item in providers)
    if representation is not None:
        ordered.append(("representation_model", representation, "representation_id"))
    ordered.append(
        (
            "corpus_research_model" if corpus_model else "research_model",
            model,
            "model_id",
        )
    )
    if training_outcome is not None:
        ordered.append(("outcome_spec", training_outcome, "outcome_id"))
    ordered.extend(("outcome_spec", item, "outcome_id") for item in evaluation_outcomes)
    ordered.extend(("decision_policy", item, "policy_id") for item in policies)
    ordered.extend(("evaluator", item, "evaluator_id") for item in evaluators)
    ordered.extend(("evaluator", item, "evaluator_id") for item in trade_evaluators)
    components: list[_ComponentSource] = []
    seen: set[str] = set()
    for kind, component, id_name in ordered:
        versioned = _typing.cast("_VersionedComponent", component)
        source = _ComponentSource(
            component_kind=kind,
            component_id=_typing.cast("str", getattr(component, id_name)),
            version=versioned.version,
            params=versioned.params,
            source_sha256=_component_source_sha256(component),
        )
        key = _canonical_identity_json(
            {
                "kind": source._component_kind,
                "id": source._origin.plugin_id,
                "version": source._origin.version,
                "params": _json.loads(_canonical_params_json(source._origin.params)),
                "source_sha256": source._source_sha256,
            }
        )
        if key not in seen:
            components.append(source)
            seen.add(key)
    return tuple(components)


def _component_source_sha256(component: object) -> str:
    path_text = _inspect.getsourcefile(type(component))
    if path_text is None:
        raise _ContractViolationError(
            f"cannot locate source for component {type(component).__qualname__}"
        )
    return _sha256_file(_Path(path_text))


def _sha256_file(path: _Path) -> str:
    if not path.is_file():
        raise _ContractViolationError(f"identity file does not exist: {path}")
    digest = _hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise _ContractViolationError(f"cannot hash identity file {path}") from exc
    return digest.hexdigest()


def _platform_evidence(repository_root: _Path) -> tuple[_PlatformIdentity, str | None]:
    commit = _git_text(repository_root, "rev-parse", "HEAD").strip()
    status = _git_bytes(
        repository_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    if not status:
        return _PlatformIdentity(commit, False, None), None
    tracked_diff = _git_bytes(repository_root, "diff", "--binary", "HEAD", "--")
    untracked = _git_bytes(
        repository_root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    untracked_entries: list[dict[str, str]] = []
    for raw_name in (name for name in untracked.split(b"\0") if name):
        try:
            name = raw_name.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _ContractViolationError("untracked Git path is not UTF-8") from exc
        relative = _Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise _ContractViolationError("Git reported an unsafe untracked path")
        candidate = repository_root / relative
        if candidate.is_symlink():
            content = _os.readlink(candidate).encode("utf-8")
            kind = "symlink"
        elif candidate.is_file():
            content = candidate.read_bytes()
            kind = "file"
        else:
            raise _ContractViolationError("Git reported an unsupported untracked entry")
        untracked_entries.append(
            {
                "kind": kind,
                "path": name,
                "sha256": _hashlib.sha256(content).hexdigest(),
            }
        )
    evidence = _canonical_identity_json(
        {
            "status_hex": status.hex(),
            "tracked_diff_hex": tracked_diff.hex(),
            "untracked": untracked_entries,
        }
    )
    digest = _hashlib.sha256(evidence.encode("utf-8")).hexdigest()
    return _PlatformIdentity(commit, True, digest), evidence


def _git_bytes(repository_root: _Path, *arguments: str) -> bytes:
    try:
        result = _subprocess.run(
            ["git", "-C", str(repository_root), *arguments],
            check=True,
            capture_output=True,
        )
    except (OSError, _subprocess.CalledProcessError) as exc:
        raise _ContractViolationError(
            f"cannot capture Git identity from {repository_root}"
        ) from exc
    return result.stdout


def _git_text(repository_root: _Path, *arguments: str) -> str:
    try:
        return _git_bytes(repository_root, *arguments).decode("ascii")
    except UnicodeDecodeError as exc:
        raise _ContractViolationError("Git identity output is not ASCII") from exc


def _lockfile_is_in_sync(repository_root: _Path) -> bool:
    try:
        result = _subprocess.run(
            ["uv", "lock", "--check"],
            cwd=repository_root,
            check=False,
            stdout=_subprocess.DEVNULL,
            stderr=_subprocess.DEVNULL,
        )
    except OSError:
        return False
    return result.returncode == 0


def run_experiment(
    config: _ResolvedExperimentConfig,
    *,
    runtime: _RuntimeEnvironment,
    unlock_gated: bool = False,
    heartbeat_interval_seconds: float = _HEARTBEAT_INTERVAL_SECONDS,
) -> _RunManifest:
    """Register and execute one foreground fixed-stage experiment."""
    prepared = _prepare_experiment(config, runtime)
    run_id = _register_new_run(
        runs_root=runtime._runs_root,
        identity=prepared._identity,
        now_unix_ns=_time.time_ns(),
    )
    manifest, _products = _execute_registered_run(
        prepared,
        runtime=runtime,
        run_id=run_id,
        unlock_gated=unlock_gated,
        verification=False,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )
    if manifest is None:  # pragma: no cover - verification=False proves this
        raise AssertionError("ordinary execution did not produce a manifest")
    return manifest


def _register_experiment(
    config: _ResolvedExperimentConfig,
    *,
    runtime: _RuntimeEnvironment,
) -> str:
    """Register one PENDING attempt without reading dataset rows."""
    prepared = _prepare_experiment(config, runtime)
    return _register_new_run(
        runs_root=runtime._runs_root,
        identity=prepared._identity,
        now_unix_ns=_time.time_ns(),
    )


def _fail_pending_run(
    *,
    runtime: _RuntimeEnvironment,
    run_id: str,
    reason: str,
) -> _RunManifest:
    """Turn a registered attempt into a permanent launcher failure."""
    _start_run(
        runs_root=runtime._runs_root,
        run_id=run_id,
        now_unix_ns=_time.time_ns(),
    )
    return _finish_after_error(
        runtime=runtime,
        run_id=run_id,
        state=_RunState.FAILED,
        reason=reason,
    )


def _run_registered_from_persisted_config(
    *,
    runtime: _RuntimeEnvironment,
    run_id: str,
    unlock_gated: bool,
    heartbeat_interval_seconds: float = _HEARTBEAT_INTERVAL_SECONDS,
) -> _RunManifest:
    config = _config_from_document(
        _read_registered_config(runs_root=runtime._runs_root, run_id=run_id)
    )
    prepared = _prepare_experiment(config, runtime)
    registered = _read_registered_identity(
        runs_root=runtime._runs_root,
        run_id=run_id,
    )
    if registered._experiment_fingerprint != prepared._identity._experiment_fingerprint:
        _start_run(
            runs_root=runtime._runs_root,
            run_id=run_id,
            now_unix_ns=_time.time_ns(),
        )
        return _finish_run(
            runs_root=runtime._runs_root,
            run_id=run_id,
            terminal_state=_RunState.FAILED,
            inventory=None,
            failure_reason="runtime identity changed after run registration",
            now_unix_ns=_time.time_ns(),
        )
    manifest, _products = _execute_registered_run(
        prepared,
        runtime=runtime,
        run_id=run_id,
        unlock_gated=unlock_gated,
        verification=False,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )
    if manifest is None:  # pragma: no cover - verification=False proves this
        raise AssertionError("ordinary execution did not produce a manifest")
    return manifest


def _execute_registered_run(
    prepared: _PreparedExperiment,
    *,
    runtime: _RuntimeEnvironment,
    run_id: str,
    unlock_gated: bool,
    verification: bool,
    heartbeat_interval_seconds: float,
) -> tuple[_RunManifest | None, _PipelineProducts | None]:
    if type(unlock_gated) is not bool or type(verification) is not bool:
        raise _ContractViolationError("execution flags must be exact bools")
    heartbeat_seconds = _validated_heartbeat_interval(heartbeat_interval_seconds)
    heartbeat_interval_ns = max(1, int(heartbeat_seconds * 1_000_000_000))
    _start_run(
        runs_root=runtime._runs_root,
        run_id=run_id,
        now_unix_ns=_time.time_ns(),
        heartbeat_interval_ns=heartbeat_interval_ns,
    )
    pump = _HeartbeatPump(
        runtime._runs_root,
        run_id,
        interval_seconds=heartbeat_interval_seconds,
    )
    pump._start()
    products: _PipelineProducts | None = None
    try:
        products = _execute_pipeline(
            prepared,
            runtime=runtime,
            run_id=run_id,
            unlock_gated=unlock_gated,
            verification=verification,
        )
        pump._stop_and_raise()
        if verification:
            return None, products
        manifest = _finish_run(
            runs_root=runtime._runs_root,
            run_id=run_id,
            terminal_state=_RunState.COMPLETE,
            inventory=None,
            failure_reason=None,
            now_unix_ns=_time.time_ns(),
        )
        return manifest, products
    except KeyboardInterrupt:
        pump._stop_without_raise()
        manifest = _finish_after_error(
            runtime=runtime,
            run_id=run_id,
            state=_RunState.INTERRUPTED,
            reason="execution interrupted by signal",
        )
        return manifest, products
    except BaseException as exc:
        pump._stop_without_raise()
        _finish_after_error(
            runtime=runtime,
            run_id=run_id,
            state=_RunState.FAILED,
            reason=_failure_reason(exc),
        )
        raise


def _finish_after_error(
    *,
    runtime: _RuntimeEnvironment,
    run_id: str,
    state: _RunState,
    reason: str,
) -> _RunManifest:
    return _finish_run(
        runs_root=runtime._runs_root,
        run_id=run_id,
        terminal_state=state,
        inventory=None,
        failure_reason=reason,
        now_unix_ns=_time.time_ns(),
    )


class _HeartbeatPump:
    __slots__ = ("_error", "_interval", "_run_id", "_runs_root", "_stop", "_thread")

    def __init__(
        self,
        runs_root: _Path,
        run_id: str,
        *,
        interval_seconds: float,
    ) -> None:
        self._runs_root = runs_root
        self._run_id = run_id
        self._interval = interval_seconds
        self._stop = _threading.Event()
        self._error: BaseException | None = None
        self._thread = _threading.Thread(
            target=self._run,
            name=f"research-heartbeat-{run_id}",
            daemon=True,
        )

    def _start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                _heartbeat_run(
                    runs_root=self._runs_root,
                    run_id=self._run_id,
                    now_unix_ns=_time.time_ns(),
                )
            except BaseException as exc:
                self._error = exc
                return

    def _stop_without_raise(self) -> None:
        self._stop.set()
        self._thread.join()

    def _stop_and_raise(self) -> None:
        self._stop_without_raise()
        if self._error is not None:
            raise _OrchestrationError("run heartbeat failed") from self._error


def _validated_heartbeat_interval(value: object) -> float:
    if type(value) not in {int, float}:
        raise _ContractViolationError("heartbeat_interval_seconds must be a finite positive number")
    numeric = _typing.cast("int | float", value)
    if not _np.isfinite(numeric) or float(numeric) <= 0.0:
        raise _ContractViolationError("heartbeat_interval_seconds must be a finite positive number")
    return float(numeric)


def _failure_reason(exc: BaseException) -> str:
    detail = " ".join(str(exc).split())
    if not detail:
        detail = "no detail"
    encoded = f"{type(exc).__name__}: {detail}"
    return encoded[:2_000].strip()


def _execute_pipeline(
    prepared: _PreparedExperiment,
    *,
    runtime: _RuntimeEnvironment,
    run_id: str,
    unlock_gated: bool,
    verification: bool,
) -> _PipelineProducts:
    grant = _authorize_run_dataset(
        prepared,
        runtime=runtime,
        run_id=run_id,
        unlock_gated=unlock_gated,
    )
    market_columns = _required_market_columns(prepared)
    source_frame = runtime._loader.load(grant, columns=market_columns)
    config = prepared._config
    unit = runtime._split_policy.time_unit
    domain = runtime._split_policy.clock_domain
    bounded = _open_bounded_view(
        loader=runtime._loader,
        grants=(grant,),
        context_start=_NativeTimeKey(config._bounds._context_start, unit, domain),
        eval_start=_NativeTimeKey(config._bounds._eval_start, unit, domain),
        eval_end=_NativeTimeKey(config._bounds._eval_end, unit, domain),
        columns=market_columns,
    )
    feature_identity = _feature_stage_identity(
        bounded,
        prepared._feature_providers,
        split_policy_identity=runtime._split_policy.identity,
        platform_identity=runtime._platform_identity,
    )
    materialization = (
        _MaterializationPolicy.EPHEMERAL
        if verification
        else _upgrade_ephemeral_for_downstream(
            _MaterializationPolicy.EPHEMERAL,
            downstream_configured=True,
        )
    )
    feature_report = (
        _verify_feature_determinism(
            feature_identity,
            lambda forced: _materialize_features(
                bounded,
                prepared._feature_providers,
                split_policy_identity=runtime._split_policy.identity,
                platform_identity=runtime._platform_identity,
                store=runtime._store,
                policy=forced,
            ),
        )
        if verification
        else None
    )
    features = _materialize_features(
        bounded,
        prepared._feature_providers,
        split_policy_identity=runtime._split_policy.identity,
        platform_identity=runtime._platform_identity,
        store=runtime._store,
        policy=materialization,
    )
    _persist_column_frame(
        runtime=runtime,
        run_id=run_id,
        name="features.npz",
        kind="features",
        identity_key=feature_identity.identity_key(),
        frame=features,
    )
    overall_view = _view_span_from_bounded_view(bounded)
    if config._frozen_state is not None:
        folds: tuple[_FoldPlan | _FrozenEvaluationPlan, ...] = (
            _FrozenEvaluationPlan(
                0,
                (0, features.num_rows),
                _KeyBounds(
                    _NativeTimeKey(int(features.keys[0]), features.unit, features.clock_domain),
                    _NativeTimeKey(int(features.keys[-1]), features.unit, features.clock_domain),
                ),
                overall_view,
            ),
        )
    else:
        if config._folds is None:  # pragma: no cover - config parser proves a mode
            raise AssertionError("fold-fit experiment has no fold config")
        training_specs = () if prepared._training_outcome is None else (prepared._training_outcome,)
        override_config = config._folds._purge_override
        override = (
            None
            if override_config is None
            else _PurgeOverride(
                override_config._purge_rows,
                override_config._reason,
            )
        )
        folds = _generate_walk_forward_folds(
            view=bounded,
            features=features,
            outcome_specs=training_specs,
            fit_rows=config._folds._fit_rows,
            eval_rows=config._folds._eval_rows,
            step_rows=config._folds._step_rows,
            embargo_rows=config._folds._embargo_rows,
            purge_override=override,
        )
    outcome_targets, outcome_comparables = _evaluation_targets(
        prepared,
        source_frame=source_frame,
        view=overall_view,
        runtime=runtime,
        run_id=run_id,
    )
    fitted_outputs: list[_FittedOutput] = []
    policy_records: list[_PolicyExecutionRecord] = []
    simulated_results: list[_SimulatedPolicyResult] = []
    comparables: list[_ComparableArtifact] = [
        _feature_comparable(
            "features",
            features,
            identity_key=feature_identity.identity_key(),
        ),
        *outcome_comparables,
    ]
    for fold in folds:
        output, records, simulated, fold_comparables = _execute_fold(
            prepared,
            runtime=runtime,
            run_id=run_id,
            source_frame=source_frame,
            features=features,
            feature_identity_key=feature_identity.identity_key(),
            fold=fold,
            materialization=materialization,
        )
        fitted_outputs.append(output)
        policy_records.extend(records)
        simulated_results.extend(simulated)
        comparables.extend(fold_comparables)
    if prepared._execution is not None:
        _persist_simulation_summary(
            prepared=prepared,
            runtime=runtime,
            run_id=run_id,
            simulated=tuple(simulated_results),
        )
    if prepared._evaluators:
        inventory = _evaluate_plane_a_inventory(
            fitted_outputs=tuple(fitted_outputs),
            outcome_targets=outcome_targets,
            evaluators=prepared._evaluators,
            policy_executions=tuple(policy_records),
        )
        comparables.extend(_metric_comparables(inventory))
    else:
        inventory = _descriptive_inventory(policy_executions=tuple(policy_records))
    if prepared._trade_evaluators:
        inventory = _evaluate_plane_b_inventory(
            base=inventory,
            simulated=tuple(simulated_results),
            evaluators=prepared._trade_evaluators,
        )
        comparables.extend(_metric_comparables(inventory, plane_b_only=True))
    _record_evaluation_inventory(
        runs_root=runtime._runs_root,
        run_id=run_id,
        inventory=inventory,
    )
    _record_run_payload(
        runs_root=runtime._runs_root,
        run_id=run_id,
        category="metrics",
        name="evaluation-summary.json",
        payload=(
            _canonical_identity_json(_evaluation_inventory_document(inventory)).encode("ascii")
            + b"\n"
        ),
    )
    return _PipelineProducts(inventory, tuple(comparables), feature_report)


def _authorize_run_dataset(
    prepared: _PreparedExperiment,
    *,
    runtime: _RuntimeEnvironment,
    run_id: str,
    unlock_gated: bool,
) -> _AccessGrant:
    """Authorize once, then permanently mark sensitive access before row loading."""
    registered = runtime._registry.resolve(prepared._config._dataset_ref)
    classification = _classify_access(registered.manifest, runtime._split_policy)
    if unlock_gated and classification is not _AccessClassification.FULLY_GATED:
        raise _ContractViolationError("--unlock-gated is legal only for a fully gated dataset")
    unlock = (
        _GatedUnlock(prepared._config._dataset_ref.canonical_identity) if unlock_gated else None
    )
    session_id = _uuid4()
    _record_run_access_attempt(
        runs_root=runtime._runs_root,
        run_id=run_id,
        session_id=str(session_id),
        now_unix_ns=_time.time_ns(),
    )
    grant = _authorize_access(
        registry=runtime._registry,
        request=_AccessRequest(
            prepared._config._dataset_ref,
            reason=prepared._config._access_reason,
            gated_unlock=unlock,
        ),
        policy=runtime._split_policy,
        context=_AccessContext(
            session_id=session_id,
            os_user=_getpass.getuser(),
            hostname=_socket.gethostname(),
            pid=_os.getpid(),
            platform_git_commit=runtime._platform_identity.git_commit,
            platform_git_dirty=runtime._platform_identity.git_dirty,
        ),
        audit_writer=runtime._audit_writer,
    )
    is_oos = classification >= _AccessClassification.OOS_EXPLICIT
    is_lockbox = classification is _AccessClassification.FULLY_GATED
    _record_run_access(
        runs_root=runtime._runs_root,
        run_id=run_id,
        oos_touched=is_oos,
        lockbox_touched=is_lockbox,
        now_unix_ns=_time.time_ns(),
        session_id=str(session_id),
        audit_event_id=grant.audit_event_id,
    )
    return grant


def _required_market_columns(prepared: _PreparedExperiment) -> tuple[str, ...]:
    names: list[str] = []
    for plan in _validated_feature_plan(prepared._feature_providers):
        for name in plan._required_columns:
            if name not in names:
                names.append(name)
    outcomes = (
        () if prepared._training_outcome is None else (prepared._training_outcome,)
    ) + prepared._evaluation_outcomes
    for outcome in outcomes:
        for name in _validated_spec(outcome).required_columns:
            if name not in names:
                names.append(name)
    if prepared._execution is not None:
        for name in ("open", "high", "low", "close"):
            if name not in names:
                names.append(name)
    if not names:
        raise _ContractViolationError("experiment requires no market columns")
    return tuple(names)


def _evaluation_targets(
    prepared: _PreparedExperiment,
    *,
    source_frame: _MarketFrame,
    view: _ViewSpan,
    runtime: _RuntimeEnvironment,
    run_id: str,
) -> tuple[tuple[_OutcomeTarget, ...], tuple[_ComparableArtifact, ...]]:
    targets: list[_OutcomeTarget] = []
    comparables: list[_ComparableArtifact] = []
    for index, spec in enumerate(prepared._evaluation_outcomes):
        frame = _compute_outcomes(source_frame, view, spec)
        identity_key = _semantic_key(
            {
                "schema": "research-outcome-artifact-identity-v1",
                "view": _view_document(view),
                "origin": _stage_origin_document(frame.origin(frame.column_names[0])),
            }
        )
        _persist_column_frame(
            runtime=runtime,
            run_id=run_id,
            name=f"outcome-{index:04d}.npz",
            kind="outcome",
            identity_key=identity_key,
            frame=frame,
        )
        targets.append(_OutcomeTarget(spec, frame))
        comparables.append(
            _column_frame_comparable(
                f"outcome-{index:04d}",
                frame,
                identity_key=identity_key,
                tolerance=_validated_spec(spec).comparison_tolerance,
            )
        )
    return tuple(targets), tuple(comparables)


def _execute_fold(
    prepared: _PreparedExperiment,
    *,
    runtime: _RuntimeEnvironment,
    run_id: str,
    source_frame: _MarketFrame,
    features: _FeatureFrame,
    feature_identity_key: str,
    fold: _FoldPlan | _FrozenEvaluationPlan,
    materialization: _MaterializationPolicy,
) -> tuple[
    _FittedOutput,
    tuple[_PolicyExecutionRecord, ...],
    tuple[_SimulatedPolicyResult, ...],
    tuple[_ComparableArtifact, ...],
]:
    if type(fold) is _FrozenEvaluationPlan:
        return _execute_frozen_fold(
            prepared,
            runtime=runtime,
            run_id=run_id,
            source_frame=source_frame,
            features=features,
            feature_identity_key=feature_identity_key,
            fold=fold,
        )
    if type(fold) is not _FoldPlan:
        raise _ContractViolationError("execution fold has an unsupported type")
    fit_features = _subset_features(features, fold._effective_fit_positions)
    eval_start, eval_stop = fold._eval_position_range
    eval_positions = tuple(range(eval_start, eval_stop))
    eval_features = _subset_features(features, eval_positions)
    model_input_plan = _snapshot_model(prepared._model)
    fit_model_features = _select_feature_columns(
        fit_features,
        model_input_plan._required_feature_names,
    )
    eval_model_features = _select_feature_columns(
        eval_features,
        model_input_plan._required_feature_names,
    )
    training_targets = _training_targets_for_fold(
        prepared,
        source_frame=source_frame,
        fold=fold,
        fit_features=fit_features,
    )
    persist_policy = materialization
    comparables: list[_ComparableArtifact] = []
    representation_state: _FittedState | None = None
    fit_representation_frame: _RepresentationFrame | None = None
    eval_representation_frame: _RepresentationFrame | None = None
    if prepared._representation is not None:
        representation_state = _fit_representation(
            prepared._representation,
            view=fold._training_view,
            features=fit_features,
            split_policy_identity=runtime._split_policy.identity,
            fit_isolation=fold._fit_isolation,
            platform_identity=runtime._platform_identity,
            dependency_lock_sha256=runtime._dependency_lock_sha256,
            store=runtime._store,
            policy=persist_policy,
        )
        fit_representation_frame = _encode_representation(
            prepared._representation,
            view=fold._training_view,
            fitted_state=representation_state,
            features=fit_features,
        )
        eval_representation_frame = _encode_representation(
            prepared._representation,
            view=fold._evaluation_view,
            fitted_state=representation_state,
            features=eval_features,
        )
        representation_plan = _snapshot_representation(prepared._representation)
        representation_frame_key = _semantic_key(
            {
                "schema": "research-representation-frame-identity-v1",
                "fitted_state_identity_key": representation_state.identity_key(),
                "view": _view_document(fold._evaluation_view),
                "feature_stage_identity_key": feature_identity_key,
            }
        )
        comparables.extend(
            (
                _fitted_state_comparable(
                    f"fold-{fold._index:04d}-representation-state",
                    representation_state,
                    tolerance=representation_plan._comparison_tolerance,
                ),
                _column_frame_comparable(
                    f"fold-{fold._index:04d}-representation-frame",
                    eval_representation_frame,
                    identity_key=representation_frame_key,
                    tolerance=representation_plan._comparison_tolerance,
                ),
            )
        )
        _persist_fitted_summary(
            runtime=runtime,
            run_id=run_id,
            name=f"fold-{fold._index:04d}-representation-state.json",
            state=representation_state,
        )
        _persist_column_frame(
            runtime=runtime,
            run_id=run_id,
            name=f"fold-{fold._index:04d}-representation-frame.npz",
            kind="representation",
            identity_key=representation_frame_key,
            frame=eval_representation_frame,
        )

    fit_inputs = _ModelInputs(fit_model_features, fit_representation_frame)
    model_state = _fit_model(
        prepared._model,
        view=fold._training_view,
        inputs=fit_inputs,
        training_targets=training_targets,
        representation_state=representation_state,
        split_policy_identity=runtime._split_policy.identity,
        fit_isolation=fold._fit_isolation,
        platform_identity=runtime._platform_identity,
        dependency_lock_sha256=runtime._dependency_lock_sha256,
        store=runtime._store,
        policy=persist_policy,
    )
    return _execute_inference_and_simulation(
        prepared,
        runtime=runtime,
        run_id=run_id,
        source_frame=source_frame,
        feature_identity_key=feature_identity_key,
        fold=fold,
        eval_features=eval_features,
        eval_model_features=eval_model_features,
        eval_representation_frame=eval_representation_frame,
        representation_state=representation_state,
        model_state=model_state,
        comparables=comparables,
    )


def _execute_frozen_fold(
    prepared: _PreparedExperiment,
    *,
    runtime: _RuntimeEnvironment,
    run_id: str,
    source_frame: _MarketFrame,
    features: _FeatureFrame,
    feature_identity_key: str,
    fold: _FrozenEvaluationPlan,
) -> tuple[
    _FittedOutput,
    tuple[_PolicyExecutionRecord, ...],
    tuple[_SimulatedPolicyResult, ...],
    tuple[_ComparableArtifact, ...],
]:
    frozen = prepared._config._frozen_state
    if frozen is None or prepared._representation is not None:
        raise _ContractViolationError(
            "frozen execution requires one frozen state and no representation"
        )
    model_plan = _snapshot_corpus_model(prepared._model)
    eval_model_features = _select_feature_columns(
        features,
        model_plan._required_feature_names,
    )
    model_state = _load_frozen_state(
        runs_root=runtime._runs_root,
        reference=_FrozenReference(
            frozen._bundle_id,
            frozen._bundle_manifest_sha256,
            frozen._fitted_state_identity_key,
            frozen._fitted_state_artifact_sha256,
        ),
        expected_model_id=model_plan._origin.plugin_id,
        expected_model_version=model_plan._origin.version,
        expected_model_params=model_plan._origin.params,
    )
    return _execute_inference_and_simulation(
        prepared,
        runtime=runtime,
        run_id=run_id,
        source_frame=source_frame,
        feature_identity_key=feature_identity_key,
        fold=fold,
        eval_features=features,
        eval_model_features=eval_model_features,
        eval_representation_frame=None,
        representation_state=None,
        model_state=model_state,
        comparables=[],
    )


def _execute_inference_and_simulation(
    prepared: _PreparedExperiment,
    *,
    runtime: _RuntimeEnvironment,
    run_id: str,
    source_frame: _MarketFrame,
    feature_identity_key: str,
    fold: _FoldPlan | _FrozenEvaluationPlan,
    eval_features: _FeatureFrame,
    eval_model_features: _FeatureFrame,
    eval_representation_frame: _RepresentationFrame | None,
    representation_state: _FittedState | None,
    model_state: _FittedState,
    comparables: list[_ComparableArtifact],
) -> tuple[
    _FittedOutput,
    tuple[_PolicyExecutionRecord, ...],
    tuple[_SimulatedPolicyResult, ...],
    tuple[_ComparableArtifact, ...],
]:
    eval_inputs = _ModelInputs(eval_model_features, eval_representation_frame)
    frozen = prepared._config._frozen_state
    def inference() -> _ModelOutputFrame:
        if frozen is None:
            return _infer_model(
                prepared._model,
                view=fold._evaluation_view,
                fitted_state=model_state,
                inputs=eval_inputs,
            )
        return _infer_corpus_model(
            prepared._model,
            view=fold._evaluation_view,
            fitted_state=model_state,
            inputs=eval_inputs,
        )
    fan_out = _infer_once_and_fan_out(
        inference=inference,
        output_column_names=prepared._model_output_names,
        policies=prepared._policies,
    )
    if (
        fan_out._counters._model_inference_calls != 1
        or fan_out._counters._model_reinference_calls != 0
        or fan_out._counters._decision_policy_calls != len(prepared._policies)
    ):
        raise AssertionError("fixed-stage fan-out counters are inconsistent")
    policy_records = _record_policy_executions(
        policies=prepared._policies,
        intents=fan_out._intents,
    )
    model_output_document: dict[str, object] = {
        "schema": "research-model-output-artifact-identity-v1",
        "fitted_state_identity_key": model_state.identity_key(),
        "representation_state_identity_key": (
            None if representation_state is None else representation_state.identity_key()
        ),
        "feature_stage_identity_key": feature_identity_key,
        "view": _view_document(fold._evaluation_view),
    }
    if frozen is not None:
        model_output_document["frozen_state_reference"] = frozen._document()
    model_output_key = _semantic_key(model_output_document)
    _persist_fitted_summary(
        runtime=runtime,
        run_id=run_id,
        name=f"fold-{fold._index:04d}-model-state.json",
        state=model_state,
    )
    _persist_column_frame(
        runtime=runtime,
        run_id=run_id,
        name=f"fold-{fold._index:04d}-model-output.npz",
        kind="model_output",
        identity_key=model_output_key,
        frame=fan_out._model_output,
    )
    model_plan = (
        _snapshot_model(prepared._model)
        if frozen is None
        else _snapshot_corpus_model(prepared._model)
    )
    comparables.extend(
        (
            _fitted_state_comparable(
                f"fold-{fold._index:04d}-model-state",
                model_state,
                tolerance=model_plan._comparison_tolerance,
            ),
            _column_frame_comparable(
                f"fold-{fold._index:04d}-model-output",
                fan_out._model_output,
                identity_key=model_output_key,
                tolerance=model_plan._comparison_tolerance,
            ),
        )
    )
    comparables.extend(
        _intent_comparable(f"fold-{fold._index:04d}-intent-{index:04d}", intent)
        for index, intent in enumerate(fan_out._intents)
    )
    simulated: list[_SimulatedPolicyResult] = []
    if prepared._execution is not None:
        request = _simulation_request_for_fold(
            source_frame=source_frame,
            fold=fold,
            expected_keys=eval_features.keys,
        )
        execution_document = _json.loads(_execution_spec_params_json(prepared._execution))
        for policy_index, (policy, intent) in enumerate(
            zip(prepared._policies, fan_out._intents, strict=True)
        ):
            policy_origin = _snapshot_policy(policy)._origin
            simulator = (
                _simulate_interruptible
                if prepared._execution.execution_id == "bracket_single_position_v1"
                else _simulate_non_interruptible
            )
            trade_log, equity_curve = simulator(
                request,
                intent,
                prepared._execution,
                fold_index=fold._index,
                policy_origin=policy_origin,
            )
            subject_document = {
                "schema": "research-simulation-artifact-identity-v1",
                "fold_index": fold._index,
                "fitted_state_identity_key": model_state.identity_key(),
                "model_output_artifact_identity_key": model_output_key,
                "policy": _stage_origin_document(policy_origin),
                "execution_spec": execution_document,
            }
            trade_key = _semantic_key({**subject_document, "artifact": "TradeLog"})
            equity_key = _semantic_key({**subject_document, "artifact": "EquityCurve"})
            _persist_intent_frame(
                runtime=runtime,
                run_id=run_id,
                name=f"fold-{fold._index:04d}-intent-{policy_index:04d}.npz",
                frame=intent,
            )
            _persist_trade_log(
                runtime=runtime,
                run_id=run_id,
                name=f"fold-{fold._index:04d}-policy-{policy_index:04d}-trades.npz",
                identity_key=trade_key,
                frame=trade_log,
            )
            _persist_equity_curve(
                runtime=runtime,
                run_id=run_id,
                name=f"fold-{fold._index:04d}-policy-{policy_index:04d}-equity.npz",
                identity_key=equity_key,
                frame=equity_curve,
            )
            simulated.append(
                _SimulatedPolicyResult(
                    _fitted_state_identity_key=model_state.identity_key(),
                    _model_output_artifact_identity_key=model_output_key,
                    _policy_origin=policy_origin,
                    _execution_spec=prepared._execution,
                    _trade_log_artifact_identity_key=trade_key,
                    _equity_curve_artifact_identity_key=equity_key,
                    _trade_log=trade_log,
                    _equity_curve=equity_curve,
                )
            )
            comparables.extend(
                (
                    _trade_log_comparable(
                        f"fold-{fold._index:04d}-policy-{policy_index:04d}-trades",
                        trade_log,
                        identity_key=trade_key,
                    ),
                    _equity_curve_comparable(
                        f"fold-{fold._index:04d}-policy-{policy_index:04d}-equity",
                        equity_curve,
                        identity_key=equity_key,
                    ),
                )
            )
    return (
        _FittedOutput(
            model_state.identity_key(),
            model_output_key,
            fan_out._model_output,
        ),
        policy_records,
        tuple(simulated),
        tuple(comparables),
    )


def _upgrade_ephemeral_for_downstream(
    policy: _MaterializationPolicy,
    *,
    downstream_configured: bool,
) -> _MaterializationPolicy:
    """Discharge ADR-0007: consumed ephemeral output must become persistent."""
    if type(policy) is not _MaterializationPolicy:
        raise _ContractViolationError("materialization policy must be exact")
    if type(downstream_configured) is not bool:
        raise _ContractViolationError("downstream_configured must be an exact bool")
    if policy is _MaterializationPolicy.EPHEMERAL and downstream_configured:
        return _MaterializationPolicy.PERSIST
    return policy


def _training_targets_for_fold(
    prepared: _PreparedExperiment,
    *,
    source_frame: _MarketFrame,
    fold: _FoldPlan,
    fit_features: _FeatureFrame,
) -> _TrainingTargetFrame | None:
    if prepared._training_outcome is None:
        return None
    computed = _compute_training_targets(
        source_frame,
        fold._training_view,
        prepared._training_outcome,
    )
    positions = _positions_for_keys(computed.keys, fit_features.keys)
    return _make_training_target_frame(
        view=fold._training_view,
        keys=computed.keys[positions],
        columns={name: computed.column(name)[positions] for name in computed.column_names},
        origins={name: computed.origin(name) for name in computed.column_names},
    )


def _simulation_request_for_fold(
    *,
    source_frame: _MarketFrame,
    fold: _FoldPlan | _FrozenEvaluationPlan,
    expected_keys: _npt.NDArray[_np.int64],
) -> _SimulationRequest:
    """Mint a bounded request from the already-authorized fold price rows."""
    frame = source_frame.slice_by_keys(
        fold._eval_key_bounds._first,
        fold._eval_key_bounds._last,
    ).select(("open", "high", "low", "close"))
    if not _np.array_equal(frame.keys, expected_keys):
        raise _ContractViolationError("simulation request axis must equal the fold evaluation axis")
    return _make_simulation_request(
        keys=frame.keys,
        unit=frame.unit,
        clock_domain=frame.clock_domain,
        source_identities=fold._evaluation_view.source_identities,
        open_values=frame.column("open"),
        high_values=frame.column("high"),
        low_values=frame.column("low"),
        close_values=frame.column("close"),
    )


def _subset_features(
    frame: _FeatureFrame,
    positions: _Sequence[int],
) -> _FeatureFrame:
    if type(frame) is not _FeatureFrame:
        raise _ContractViolationError("feature subset requires an exact FeatureFrame")
    index = _validated_positions(positions, upper=frame.num_rows)
    return _restore_feature_frame(
        keys=frame.keys[index],
        unit=frame.unit,
        clock_domain=frame.clock_domain,
        source_identities=frame.source_identities,
        columns={name: frame.column(name)[index] for name in frame.column_names},
        origins={name: frame.origin(name) for name in frame.column_names},
    )


def _select_feature_columns(
    frame: _FeatureFrame,
    column_names: tuple[str, ...],
) -> _FeatureFrame:
    """Project a provider superset onto one model's exact declared inputs."""
    if type(frame) is not _FeatureFrame:
        raise _ContractViolationError("feature selection requires an exact FeatureFrame")
    if (
        type(column_names) is not tuple
        or not column_names
        or len(set(column_names)) != len(column_names)
        or any(name not in frame.column_names for name in column_names)
    ):
        raise _ContractViolationError(
            "selected feature columns must be a non-empty available unique tuple"
        )
    return _restore_feature_frame(
        keys=frame.keys,
        unit=frame.unit,
        clock_domain=frame.clock_domain,
        source_identities=frame.source_identities,
        columns={name: frame.column(name) for name in column_names},
        origins={name: frame.origin(name) for name in column_names},
    )


def _validated_positions(
    positions: _Sequence[int],
    *,
    upper: int,
) -> _npt.NDArray[_np.int64]:
    if isinstance(positions, str | bytes) or not isinstance(positions, _Sequence):
        raise _ContractViolationError("positions must be an ordered sequence")
    values = tuple(positions)
    if (
        not values
        or any(type(value) is not int or value < 0 or value >= upper for value in values)
        or any(right <= left for left, right in _pairwise(values))
    ):
        raise _ContractViolationError(
            "positions must be non-empty, strictly increasing, and in bounds"
        )
    return _np.asarray(values, dtype=_np.int64)


def _positions_for_keys(
    available: _npt.NDArray[_np.int64],
    requested: _npt.NDArray[_np.int64],
) -> _npt.NDArray[_np.int64]:
    positions = _np.searchsorted(available, requested)
    if (
        positions.size != requested.size
        or _np.any(positions >= available.size)
        or not _np.array_equal(available[positions], requested)
    ):
        raise _ContractViolationError("requested fit keys are unavailable in targets")
    return positions


def _persist_fitted_summary(
    *,
    runtime: _RuntimeEnvironment,
    run_id: str,
    name: str,
    state: _FittedState,
) -> None:
    arrays = []
    for array_name in state.payload.array_names:
        values = state.payload.array(array_name)
        arrays.append(
            {
                "name": array_name,
                "dtype": values.dtype.str,
                "shape": list(values.shape),
                "content_sha256": _hashlib.sha256(values.tobytes(order="C")).hexdigest(),
            }
        )
    document = {
        "schema": "research-fitted-state-reference-v1",
        "identity_key": state.identity_key(),
        "metadata": _json.loads(_canonical_params_json(state.payload.metadata)),
        "arrays": arrays,
    }
    _record_run_payload(
        runs_root=runtime._runs_root,
        run_id=run_id,
        category="artifacts",
        name=name,
        payload=_canonical_identity_json(document).encode("ascii") + b"\n",
    )


def _persist_column_frame(
    *,
    runtime: _RuntimeEnvironment,
    run_id: str,
    name: str,
    kind: str,
    identity_key: str,
    frame: _FeatureFrame | _RepresentationFrame | _ModelOutputFrame | _OutcomeFrame,
) -> None:
    column_documents = []
    members: dict[str, _npt.NDArray[_typing.Any]] = {}
    for index, column_name in enumerate(frame.column_names):
        member = f"column_{index:04d}"
        values = frame.column(column_name)
        column_documents.append(
            {
                "name": column_name,
                "member": member,
                "dtype": values.dtype.str,
                "origin": _origin_document(frame.origin(column_name)),
            }
        )
        members[member] = values
    metadata = {
        "format": "trading-research-run-frame",
        "format_version": _ARTIFACT_FORMAT_VERSION,
        "kind": kind,
        "identity_key": identity_key,
        "axis": _frame_axis_document(frame),
        "columns": column_documents,
    }
    metadata_bytes = _canonical_identity_json(metadata).encode("ascii")
    arrays: dict[str, _npt.NDArray[_typing.Any]] = {
        "__meta__": _np.frombuffer(metadata_bytes, dtype=_np.uint8),
        "keys": frame.keys,
        **members,
    }
    output = _io.BytesIO()
    expanded_bytes = sum(int(values.nbytes) for values in arrays.values())
    compressed = expanded_bytes > _RUN_FILE_LIMIT_BYTES
    if expanded_bytes > _RUN_FRAME_EXPANDED_LIMIT_BYTES:
        raise _ContractViolationError(
            "run column frame exceeds the 512-MiB expanded archive limit"
        )
    encoder = _np.savez_compressed if compressed else _np.savez
    _typing.cast("_typing.Callable[..., None]", encoder)(output, **arrays)
    payload = output.getvalue()
    _validate_zip_envelope(
        payload,
        tuple(arrays),
        compressed=compressed,
        max_uncompressed_bytes=_RUN_FRAME_EXPANDED_LIMIT_BYTES,
    )
    _record_run_payload(
        runs_root=runtime._runs_root,
        run_id=run_id,
        category="artifacts",
        name=name,
        payload=payload,
    )


def _persist_closed_npz(
    *,
    runtime: _RuntimeEnvironment,
    run_id: str,
    name: str,
    metadata: _Mapping[str, object],
    arrays: _Mapping[str, _npt.NDArray[_typing.Any]],
) -> None:
    metadata_bytes = _canonical_identity_json(metadata).encode("ascii")
    members: dict[str, _npt.NDArray[_typing.Any]] = {
        "__meta__": _np.frombuffer(metadata_bytes, dtype=_np.uint8),
        **arrays,
    }
    output = _io.BytesIO()
    _typing.cast("_typing.Callable[..., None]", _np.savez)(output, **members)
    payload = output.getvalue()
    _validate_zip_envelope(payload, tuple(members))
    _record_run_payload(
        runs_root=runtime._runs_root,
        run_id=run_id,
        category="artifacts",
        name=name,
        payload=payload,
    )


def _persist_intent_frame(
    *,
    runtime: _RuntimeEnvironment,
    run_id: str,
    name: str,
    frame: _IntentFrame,
) -> None:
    arrays: dict[str, _npt.NDArray[_typing.Any]] = {
        "keys": frame.keys,
        "target_exposure": frame.target_exposure,
    }
    # The intent container needs no format bump: intent_schema_id already tells a
    # reader which channels to expect, and a reader that does not know the
    # bracketed schema refuses on the id before it looks for an array.
    take_profit = frame.take_profit_distance
    stop_loss = frame.stop_loss_distance
    if take_profit is not None and stop_loss is not None:
        arrays["take_profit_distance"] = take_profit
        arrays["stop_loss_distance"] = stop_loss
    _persist_closed_npz(
        runtime=runtime,
        run_id=run_id,
        name=name,
        metadata={
            "format": "trading-research-intent-frame",
            "format_version": _ARTIFACT_FORMAT_VERSION,
            "intent_schema_id": frame.intent_schema_id,
            "time_unit": frame.unit.value,
            "clock_domain": frame.clock_domain.identifier,
        },
        arrays=arrays,
    )


def _persist_trade_log(
    *,
    runtime: _RuntimeEnvironment,
    run_id: str,
    name: str,
    identity_key: str,
    frame: _TradeLog,
) -> None:
    column_names = (
        "entry_key",
        "exit_key",
        "direction",
        "entry_price",
        "exit_price",
        "exit_reason",
        "ambiguous_exit",
        "gapped_exit",
        "gross_pnl",
        "spread_cost",
        "commission_cost",
        "net_pnl",
        "take_profit_price",
        "stop_loss_price",
    )
    arrays = {name: getattr(frame, name) for name in column_names}
    _persist_closed_npz(
        runtime=runtime,
        run_id=run_id,
        name=name,
        metadata={
            "format": "trading-research-trade-log",
            "format_version": _TRADE_LOG_FORMAT_VERSION,
            "identity_key": identity_key,
            "time_unit": frame.unit.value,
            "clock_domain": frame.clock_domain.identifier,
            "source_identities": _source_identity_documents(frame.source_identities),
            "fold_index": frame.fold_index,
            "policy_origin": _stage_origin_document(frame.policy_origin),
            "intent_schema_id": frame.intent_schema_id,
            "execution_spec": _json.loads(_execution_spec_params_json(frame.execution_spec)),
            "execution_rows": frame.execution_rows,
            "counters": _trade_counter_document(frame),
            "columns": [
                {"name": column_name, "dtype": arrays[column_name].dtype.str}
                for column_name in column_names
            ],
        },
        arrays=arrays,
    )


def _persist_equity_curve(
    *,
    runtime: _RuntimeEnvironment,
    run_id: str,
    name: str,
    identity_key: str,
    frame: _EquityCurve,
) -> None:
    _persist_closed_npz(
        runtime=runtime,
        run_id=run_id,
        name=name,
        metadata={
            "format": "trading-research-equity-curve",
            "format_version": _ARTIFACT_FORMAT_VERSION,
            "identity_key": identity_key,
            "time_unit": frame.unit.value,
            "clock_domain": frame.clock_domain.identifier,
            "source_identities": _source_identity_documents(frame.source_identities),
            "fold_index": frame.fold_index,
            "policy_origin": _stage_origin_document(frame.policy_origin),
            "num_rows": frame.num_rows,
        },
        arrays={"keys": frame.keys, "equity": frame.equity},
    )


def _trade_counter_document(frame: _TradeLog) -> dict[str, int]:
    return {
        "intent_rows_long": frame.intent_rows_long,
        "intent_rows_flat": frame.intent_rows_flat,
        "intent_rows_short": frame.intent_rows_short,
        "intent_rows_abstain": frame.intent_rows_abstain,
        "intent_rows_absent": frame.intent_rows_absent,
        "unexecuted_final_row_intents": frame.unexecuted_final_row_intents,
    }


def _source_identity_documents(
    value: tuple[_DatasetIdentity, ...],
) -> list[dict[str, object]]:
    return [
        {
            "dataset_id": identity.dataset_id,
            "manifest_sha256": identity.manifest_sha256,
        }
        for identity in value
    ]


def _persist_simulation_summary(
    *,
    prepared: _PreparedExperiment,
    runtime: _RuntimeEnvironment,
    run_id: str,
    simulated: tuple[_SimulatedPolicyResult, ...],
) -> None:
    spec = prepared._execution
    if spec is None:
        raise AssertionError("simulation summary requires execution")
    binding = spec.cost_binding
    document = {
        "schema": "research-simulation-summary-v1",
        "execution_spec": _json.loads(_execution_spec_params_json(spec)),
        "cost_semantics": binding.cost_semantics.value,
        "cost_semantics_unverified": binding.cost_semantics.value == "unverified",
        "binding_id": binding.binding_id,
        "binding_digest": binding.binding_digest,
        "execution_price_model": binding.execution_price_model.value,
        "results": [
            {
                "fitted_state_identity": result._fitted_state_identity_key,
                "model_output_artifact_identity": (result._model_output_artifact_identity_key),
                "policy": _stage_origin_document(result._policy_origin),
                "fold_index": result._trade_log.fold_index,
                "trade_log_artifact_identity": (result._trade_log_artifact_identity_key),
                "equity_curve_artifact_identity": (result._equity_curve_artifact_identity_key),
                "num_trades": result._trade_log.num_trades,
                "execution_rows": result._trade_log.execution_rows,
                "counters": _trade_counter_document(result._trade_log),
                "ambiguous_exit_count": int(_np.count_nonzero(result._trade_log.ambiguous_exit)),
                "gapped_exit_count": int(_np.count_nonzero(result._trade_log.gapped_exit)),
                "boundary_forced_close_count": int(
                    _np.count_nonzero(
                        result._trade_log.exit_reason
                        == _trade_exit_reason_code(_TradeExitReason.BOUNDARY_FORCED_CLOSE)
                    )
                ),
            }
            for result in simulated
        ],
    }
    _record_run_payload(
        runs_root=runtime._runs_root,
        run_id=run_id,
        category="metrics",
        name="simulation-summary.json",
        payload=_canonical_identity_json(document).encode("ascii") + b"\n",
    )


def _feature_comparable(
    label: str,
    frame: _FeatureFrame,
    *,
    identity_key: str,
) -> _ComparableArtifact:
    return _column_frame_comparable(
        label,
        frame,
        identity_key=identity_key,
        tolerance=None,
    )


def _column_frame_comparable(
    label: str,
    frame: _FeatureFrame | _RepresentationFrame | _ModelOutputFrame | _OutcomeFrame,
    *,
    identity_key: str,
    tolerance: _ToleranceSpec | None,
) -> _ComparableArtifact:
    columns = tuple(frame.column(name) for name in frame.column_names)
    structure = {
        "kind": type(frame).__name__,
        "identity_key": identity_key,
        "axis": _frame_axis_document(frame),
        "columns": [
            {
                "name": name,
                "dtype": frame.column(name).dtype.str,
                "shape": list(frame.column(name).shape),
                "origin": _origin_document(frame.origin(name)),
            }
            for name in frame.column_names
        ],
        "tolerance": _tolerance_document(tolerance),
    }
    exact, floating = _partition_arrays(columns)
    return _ComparableArtifact(
        label,
        _canonical_identity_json(structure),
        (frame.keys, *exact),
        floating,
        tolerance,
    )


def _fitted_state_comparable(
    label: str,
    state: _FittedState,
    *,
    tolerance: _ToleranceSpec | None,
) -> _ComparableArtifact:
    arrays = tuple(state.payload.array(name) for name in state.payload.array_names)
    exact, floating = _partition_arrays(arrays)
    structure = {
        "kind": "FittedState",
        "identity_key": state.identity_key(),
        "metadata": _json.loads(_canonical_params_json(state.payload.metadata)),
        "arrays": [
            {
                "name": name,
                "dtype": state.payload.array(name).dtype.str,
                "shape": list(state.payload.array(name).shape),
            }
            for name in state.payload.array_names
        ],
        "tolerance": _tolerance_document(tolerance),
    }
    return _ComparableArtifact(
        label,
        _canonical_identity_json(structure),
        exact,
        floating,
        tolerance,
    )


def _intent_comparable(label: str, frame: _IntentFrame) -> _ComparableArtifact:
    take_profit = frame.take_profit_distance
    stop_loss = frame.stop_loss_distance
    floating: tuple[_npt.NDArray[_typing.Any], ...] = (frame.target_exposure,)
    if take_profit is not None and stop_loss is not None:
        floating = (frame.target_exposure, take_profit, stop_loss)
    structure = {
        "kind": "IntentFrame",
        "intent_schema_id": frame.intent_schema_id,
        "time_unit": frame.unit.value,
        "clock_domain": frame.clock_domain.identifier,
        "key_shape": list(frame.keys.shape),
        "value_dtype": frame.target_exposure.dtype.str,
        "value_shape": list(frame.target_exposure.shape),
        "bracket_channels": len(floating) - 1,
        "tolerance": None,
    }
    return _ComparableArtifact(
        label,
        _canonical_identity_json(structure),
        (frame.keys,),
        floating,
        None,
    )


def _trade_log_comparable(
    label: str,
    frame: _TradeLog,
    *,
    identity_key: str,
) -> _ComparableArtifact:
    structure = {
        "kind": "TradeLog",
        "identity_key": identity_key,
        "fold_index": frame.fold_index,
        "policy_origin": _stage_origin_document(frame.policy_origin),
        "intent_schema_id": frame.intent_schema_id,
        "execution_spec": _json.loads(_execution_spec_params_json(frame.execution_spec)),
        "execution_rows": frame.execution_rows,
        "num_trades": frame.num_trades,
        "counters": _trade_counter_document(frame),
        "tolerance": None,
    }
    return _ComparableArtifact(
        label,
        _canonical_identity_json(structure),
        (
            frame.entry_key,
            frame.exit_key,
            frame.direction,
            frame.exit_reason,
            frame.ambiguous_exit,
            frame.gapped_exit,
        ),
        (
            frame.entry_price,
            frame.exit_price,
            frame.gross_pnl,
            frame.spread_cost,
            frame.commission_cost,
            frame.net_pnl,
            frame.take_profit_price,
            frame.stop_loss_price,
        ),
        None,
    )


def _equity_curve_comparable(
    label: str,
    frame: _EquityCurve,
    *,
    identity_key: str,
) -> _ComparableArtifact:
    structure = {
        "kind": "EquityCurve",
        "identity_key": identity_key,
        "fold_index": frame.fold_index,
        "policy_origin": _stage_origin_document(frame.policy_origin),
        "num_rows": frame.num_rows,
        "tolerance": None,
    }
    return _ComparableArtifact(
        label,
        _canonical_identity_json(structure),
        (frame.keys,),
        (frame.equity,),
        None,
    )


def _metric_comparables(
    inventory: _EvaluationInventory,
    *,
    plane_b_only: bool = False,
) -> tuple[_ComparableArtifact, ...]:
    results: list[_ComparableArtifact] = []
    plane_a_entries = () if plane_b_only else inventory._entries
    for entry_index, entry in enumerate(plane_a_entries):
        for metric in entry._metrics:
            structure = {
                "kind": "MetricValue",
                "entry_index": entry_index,
                "fitted_state_identity_key": entry._fitted_state_identity_key,
                "model_output_artifact_identity_key": (entry._model_output_artifact_identity_key),
                "evaluated_output_columns": list(entry._evaluated_output_columns),
                "outcome_origin": _stage_origin_document(entry._outcome_origin),
                "horizon_rows": entry._horizon_rows,
                "decision_delay_rows": entry._decision_delay_rows,
                "evaluator_origin": _stage_origin_document(entry._evaluator_origin),
                "name": metric.name,
                "effective_samples": metric.effective_samples,
                "tolerance": _tolerance_document(entry._comparison_tolerance),
            }
            results.append(
                _ComparableArtifact(
                    f"metric-{entry_index:04d}-{metric.name}",
                    _canonical_identity_json(structure),
                    (),
                    (_np.asarray([metric.value], dtype=_np.float64),),
                    entry._comparison_tolerance,
                )
            )
    for entry_index, trade_entry in enumerate(inventory._trade_entries):
        for metric in trade_entry._metrics:
            structure = {
                "kind": "MetricValue",
                "plane": "B",
                "entry_index": entry_index,
                "fitted_state_identity_key": trade_entry._fitted_state_identity_key,
                "model_output_artifact_identity_key": (
                    trade_entry._model_output_artifact_identity_key
                ),
                "policy_origin": _stage_origin_document(trade_entry._policy_origin),
                "execution_spec": _json.loads(
                    _execution_spec_params_json(trade_entry._execution_spec)
                ),
                "trade_log_artifact_identity_key": (trade_entry._trade_log_artifact_identity_key),
                "equity_curve_artifact_identity_key": (
                    trade_entry._equity_curve_artifact_identity_key
                ),
                "evaluator_origin": _stage_origin_document(trade_entry._evaluator_origin),
                "name": metric.name,
                "effective_samples": metric.effective_samples,
                "tolerance": None,
            }
            results.append(
                _ComparableArtifact(
                    f"trade-metric-{entry_index:04d}-{metric.name}",
                    _canonical_identity_json(structure),
                    (),
                    (_np.asarray([metric.value], dtype=_np.float64),),
                    None,
                )
            )
    return tuple(results)


def _partition_arrays(
    arrays: _Sequence[_npt.NDArray[_typing.Any]],
) -> tuple[
    tuple[_npt.NDArray[_typing.Any], ...],
    tuple[_npt.NDArray[_typing.Any], ...],
]:
    exact: list[_npt.NDArray[_typing.Any]] = []
    floating: list[_npt.NDArray[_typing.Any]] = []
    for values in arrays:
        if _np.issubdtype(values.dtype, _np.floating):
            floating.append(values)
        else:
            exact.append(values)
    return tuple(exact), tuple(floating)


def _frame_axis_document(
    frame: _FeatureFrame | _RepresentationFrame | _ModelOutputFrame | _OutcomeFrame,
) -> dict[str, object]:
    return {
        "time_unit": frame.unit.value,
        "clock_domain": frame.clock_domain.identifier,
        "row_count": frame.num_rows,
        "key_dtype": frame.keys.dtype.str,
        "key_shape": list(frame.keys.shape),
        "source_identities": [
            {
                "dataset_id": identity.dataset_id,
                "manifest_sha256": identity.manifest_sha256,
            }
            for identity in frame.source_identities
        ],
    }


def _origin_document(origin: object) -> dict[str, object]:
    if type(origin) is _StageOrigin:
        return _stage_origin_document(origin)
    provider_id = getattr(origin, "provider_id", None)
    version = getattr(origin, "version", None)
    params = getattr(origin, "params", None)
    window = getattr(origin, "window", None)
    if (
        type(provider_id) is not str
        or type(version) is not int
        or not isinstance(params, _Mapping)
        or window is None
    ):
        raise _ContractViolationError("frame origin is unsupported")
    return {
        "provider_id": provider_id,
        "version": version,
        "params": _json.loads(
            _canonical_params_json(_typing.cast("_Mapping[str, _ParamValue]", params))
        ),
        "window": {
            "max_prior_rows": window.max_prior_rows,
            "gap_policy": window.gap_policy.value,
            "nominal_interval": window.nominal_interval,
        },
    }


def _stage_origin_document(origin: _StageOrigin) -> dict[str, object]:
    return {
        "plugin_id": origin.plugin_id,
        "version": origin.version,
        "params": _json.loads(_canonical_params_json(origin.params)),
    }


def _tolerance_document(tolerance: _ToleranceSpec | None) -> object:
    if tolerance is None:
        return None
    return {
        "atol": tolerance.atol,
        "rtol": tolerance.rtol,
        "reason": tolerance.reason,
    }


def _view_document(view: _ViewSpan) -> dict[str, object]:
    return {
        "source_identities": [
            {
                "dataset_id": identity.dataset_id,
                "manifest_sha256": identity.manifest_sha256,
            }
            for identity in view.source_identities
        ],
        "time_unit": view.unit.value,
        "clock_domain": view.clock_domain.identifier,
        "context_start": view.context_start.value,
        "eval_start": view.eval_start.value,
        "eval_end": view.eval_end.value,
    }


def _semantic_key(document: _Mapping[str, object]) -> str:
    return _hashlib.sha256(_canonical_identity_json(document).encode("ascii")).hexdigest()


def verify_experiment(
    config: _ResolvedExperimentConfig,
    *,
    runtime: _RuntimeEnvironment,
    unlock_gated: bool = False,
    heartbeat_interval_seconds: float = _HEARTBEAT_INTERVAL_SECONDS,
) -> dict[str, object]:
    """Recompute two attempts under one fingerprint with every cache bypassed."""
    prepared = _prepare_experiment(config, runtime)
    first_run_id = _register_new_run(
        runs_root=runtime._runs_root,
        identity=prepared._identity,
        now_unix_ns=_time.time_ns(),
    )
    second_run_id = _register_new_run(
        runs_root=runtime._runs_root,
        identity=prepared._identity,
        now_unix_ns=_time.time_ns(),
    )
    first_left_running = False
    second_invoked = False
    second_left_running = False
    try:
        first_manifest, first = _execute_registered_run(
            prepared,
            runtime=runtime,
            run_id=first_run_id,
            unlock_gated=unlock_gated,
            verification=True,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )
        if first_manifest is not None or first is None:
            raise _OrchestrationError("first verification execution was interrupted")
        first_left_running = True
        second_invoked = True
        second_manifest, second = _execute_registered_run(
            prepared,
            runtime=runtime,
            run_id=second_run_id,
            unlock_gated=unlock_gated,
            verification=True,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )
        if second_manifest is not None or second is None:
            raise _OrchestrationError("second verification execution was interrupted")
        second_left_running = True
        report = _determinism_report(
            fingerprint=prepared._identity._experiment_fingerprint,
            first_run_id=first_run_id,
            second_run_id=second_run_id,
            first=first,
            second=second,
        )
        _record_determinism_report(
            runs_root=runtime._runs_root,
            run_id=first_run_id,
            report=report,
        )
        _record_determinism_report(
            runs_root=runtime._runs_root,
            run_id=second_run_id,
            report=report,
        )
        passed = _typing.cast("bool", report["passed"])
        state = _RunState.COMPLETE if passed else _RunState.FAILED
        reason = None if passed else "determinism verification failed"
        _finish_run(
            runs_root=runtime._runs_root,
            run_id=first_run_id,
            terminal_state=state,
            inventory=None,
            failure_reason=reason,
            now_unix_ns=_time.time_ns(),
        )
        first_left_running = False
        _finish_run(
            runs_root=runtime._runs_root,
            run_id=second_run_id,
            terminal_state=state,
            inventory=None,
            failure_reason=reason,
            now_unix_ns=_time.time_ns(),
        )
        second_left_running = False
        return report
    except BaseException as exc:
        reason = f"verification aborted: {_failure_reason(exc)}"
        if first_left_running:
            _finish_after_error(
                runtime=runtime,
                run_id=first_run_id,
                state=_RunState.FAILED,
                reason=reason,
            )
        if second_left_running:
            _finish_after_error(
                runtime=runtime,
                run_id=second_run_id,
                state=_RunState.FAILED,
                reason=reason,
            )
        elif not second_invoked:
            _start_run(
                runs_root=runtime._runs_root,
                run_id=second_run_id,
                now_unix_ns=_time.time_ns(),
            )
            _finish_after_error(
                runtime=runtime,
                run_id=second_run_id,
                state=_RunState.FAILED,
                reason=reason,
            )
        raise


def _determinism_report(
    *,
    fingerprint: str,
    first_run_id: str,
    second_run_id: str,
    first: _PipelineProducts,
    second: _PipelineProducts,
) -> dict[str, object]:
    first_feature = first._feature_report
    second_feature = second._feature_report
    if first_feature is None or second_feature is None:
        raise AssertionError("verification pipeline omitted its feature report")
    artifact_reports, artifacts_passed, maximum = _compare_bundles(
        first._comparables,
        second._comparables,
    )
    inventory_equal = _canonical_identity_json(
        _evaluation_inventory_document(first._inventory)
    ) == _canonical_identity_json(_evaluation_inventory_document(second._inventory))
    feature_passed = first_feature.deterministic and second_feature.deterministic
    passed = feature_passed and inventory_equal and artifacts_passed
    return {
        "schema": _REPORT_SCHEMA,
        "experiment_fingerprint": fingerprint,
        "run_ids": [first_run_id, second_run_id],
        "cache_policy": "forced_ephemeral",
        "passed": passed,
        "structural_equality": inventory_equal
        and all(_typing.cast("bool", item["structural_equal"]) for item in artifact_reports),
        "first_content_hash": _bundle_hash(first._comparables),
        "second_content_hash": _bundle_hash(second._comparables),
        "maximum_observed_absolute_deviation": maximum,
        "feature_stage": {
            "first": _feature_report_document(first_feature),
            "second": _feature_report_document(second_feature),
        },
        "evaluation_inventory_equal": inventory_equal,
        "artifacts": artifact_reports,
    }


def _feature_report_document(report: _FeatureReport) -> dict[str, object]:
    return {
        "identity_key": report.identity_key,
        "first_content_hash": report.first_content_hash,
        "second_content_hash": report.second_content_hash,
        "structurally_equal": report.structurally_equal,
        "deterministic": report.deterministic,
    }


def _compare_bundles(
    first: tuple[_ComparableArtifact, ...],
    second: tuple[_ComparableArtifact, ...],
) -> tuple[list[dict[str, object]], bool, float | str | None]:
    reports: list[dict[str, object]] = []
    passed = len(first) == len(second)
    maximum: float | str | None = None
    pair_count = min(len(first), len(second))
    for index in range(pair_count):
        report, artifact_passed, observed = _compare_artifact(first[index], second[index])
        reports.append(report)
        passed = passed and artifact_passed
        maximum = _maximum_deviation(maximum, observed)
    if len(first) != len(second):
        reports.append(
            {
                "label": "bundle-cardinality",
                "structural_equal": False,
                "passed": False,
                "first_count": len(first),
                "second_count": len(second),
                "tolerance": None,
                "observed_max_absolute_deviation": None,
            }
        )
    return reports, passed, maximum


def _compare_artifact(
    left: _ComparableArtifact,
    right: _ComparableArtifact,
) -> tuple[dict[str, object], bool, float | str | None]:
    structural = (
        left._label == right._label
        and left._structure_json == right._structure_json
        and len(left._exact_arrays) == len(right._exact_arrays)
        and len(left._floating_arrays) == len(right._floating_arrays)
        and left._tolerance == right._tolerance
    )
    exact_equal = structural and all(
        _arrays_bitwise_equal(a, b)
        for a, b in zip(left._exact_arrays, right._exact_arrays, strict=True)
    )
    floating_equal = structural
    observed: float | str | None = None
    if structural:
        for first, second in zip(
            left._floating_arrays,
            right._floating_arrays,
            strict=True,
        ):
            equal, deviation = _floating_arrays_equal(
                first,
                second,
                tolerance=left._tolerance,
            )
            floating_equal = floating_equal and equal
            observed = _maximum_deviation(observed, deviation)
    passed = structural and exact_equal and floating_equal
    return (
        {
            "label": left._label,
            "structural_equal": structural,
            "exact_arrays_equal": exact_equal,
            "floating_arrays_equal": floating_equal,
            "tolerance": _tolerance_document(left._tolerance),
            "observed_max_absolute_deviation": observed,
            "passed": passed,
        },
        passed,
        observed,
    )


def _arrays_bitwise_equal(
    left: _npt.NDArray[_typing.Any],
    right: _npt.NDArray[_typing.Any],
) -> bool:
    return (
        left.dtype == right.dtype
        and left.shape == right.shape
        and left.tobytes(order="C") == right.tobytes(order="C")
    )


def _floating_arrays_equal(
    left: _npt.NDArray[_typing.Any],
    right: _npt.NDArray[_typing.Any],
    *,
    tolerance: _ToleranceSpec | None,
) -> tuple[bool, float | str | None]:
    if left.dtype != right.dtype or left.shape != right.shape:
        return False, None
    if tolerance is None:
        return _arrays_bitwise_equal(left, right), _observed_deviation(left, right)
    left_nan = _np.isnan(left)
    right_nan = _np.isnan(right)
    if left_nan.tobytes(order="C") != right_nan.tobytes(order="C"):
        return False, None
    finite = ~left_nan
    if not finite.any():
        return True, 0.0
    left_values = left[finite].astype(_np.longdouble)
    right_values = right[finite].astype(_np.longdouble)
    deviation = _np.abs(left_values - right_values)
    limits = _np.longdouble(tolerance.atol) + _np.longdouble(tolerance.rtol) * _np.abs(right_values)
    observed = _finite_deviation(deviation)
    return bool(_np.all(deviation <= limits)), observed


def _observed_deviation(
    left: _npt.NDArray[_typing.Any],
    right: _npt.NDArray[_typing.Any],
) -> float | str | None:
    left_nan = _np.isnan(left)
    right_nan = _np.isnan(right)
    if left_nan.tobytes(order="C") != right_nan.tobytes(order="C"):
        return None
    finite = ~left_nan
    if not finite.any():
        return 0.0
    return _finite_deviation(
        _np.abs(left[finite].astype(_np.longdouble) - right[finite].astype(_np.longdouble))
    )


def _finite_deviation(values: _npt.NDArray[_typing.Any]) -> float | str:
    maximum = _np.max(values)
    converted = float(maximum)
    return converted if _np.isfinite(converted) else "overflow"


def _maximum_deviation(
    left: float | str | None,
    right: float | str | None,
) -> float | str | None:
    if left == "overflow" or right == "overflow":
        return "overflow"
    if left is None:
        return right
    if right is None:
        return left
    return max(_typing.cast("float", left), _typing.cast("float", right))


def _bundle_hash(artifacts: tuple[_ComparableArtifact, ...]) -> str:
    digest = _hashlib.sha256()
    for artifact in artifacts:
        digest.update(artifact._label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(artifact._structure_json.encode("ascii"))
        for category, arrays in (
            (b"exact", artifact._exact_arrays),
            (b"floating", artifact._floating_arrays),
        ):
            digest.update(category)
            for values in arrays:
                digest.update(values.dtype.str.encode("ascii"))
                digest.update(_canonical_identity_json(list(values.shape)).encode("ascii"))
                digest.update(values.tobytes(order="C"))
    return digest.hexdigest()
