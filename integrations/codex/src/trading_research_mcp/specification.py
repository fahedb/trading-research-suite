"""Experiment agreements and immutable revisions; never an execution authority."""

from __future__ import annotations

import fcntl
import re
import time
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from trading_research_mcp.service import Refused, Settings, atomic_json, digest, encoded, read_json

Text = Annotated[str, Field(min_length=1, max_length=8000)]
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class DataNeed(Document):
    instrument: Text | None = None
    timeframe: Text | None = None
    period: Text | None = None
    clock_domain: Text | None = None
    time_unit: Literal["seconds", "milliseconds", "microseconds", "nanoseconds"] | None = None
    role: Literal["train", "validation"] | None = None
    source_type: Literal["admitted", "personal_file", "catalog", "external_timed_feed"] | None = (
        None
    )
    source_description: Text | None = None
    dataset_id: Text | None = None
    manifest_sha256: Digest | None = None
    required_columns: list[Text] = Field(default_factory=list, max_length=100)
    missing_observations: Text | None = None


class ComponentNeed(Document):
    name: Text
    kind: Literal["feature", "representation", "model", "decision", "outcome", "evaluator", "data"]
    definition: Text | None = None
    inputs: list[Text] = Field(default_factory=list, max_length=100)
    availability: Literal["unknown", "available", "derive", "implement", "obtain", "unsupported"]
    evidence: Text | None = None


class SimulationNeed(Document):
    entry_rule: Text | None = None
    exit_rule: Text | None = None
    sizing: Text | None = None
    position_overlap: Text | None = None
    fill_timing: Text | None = None
    conflict_rule: Text | None = None
    costs: Text | None = None
    methodology_basis: Text | None = None


class EvaluationNeed(Document):
    metric: Text | None = None
    baseline: Text | None = None
    success_criterion: Text | None = None
    split_plan: Text | None = None
    stopping_rule: Text | None = None
    max_runs: Annotated[int, Field(strict=True, ge=1, le=1000)] | None = None
    resource_limits: Text | None = None


class ExperimentSpec(Document):
    schema_version: Literal["experiment-spec-v1"] = "experiment-spec-v1"
    experiment_id: Identifier
    revision: Annotated[int, Field(strict=True, ge=1, le=9999)] = 1
    supersedes_sha256: Digest | None = None
    idea: Text
    kind: Literal["predictive", "simulation", "descriptive"] | None = None
    question: Text | None = None
    hypothesis: Text | None = None
    signal_rule: Text | None = None
    observation_timing: Text | None = None
    outcome_and_horizon: Text | None = None
    data: DataNeed = Field(default_factory=DataNeed)
    components: list[ComponentNeed] = Field(default_factory=list, max_length=100)
    simulation: SimulationNeed | None = None
    evaluation: EvaluationNeed = Field(default_factory=EvaluationNeed)
    open_questions: list[Text] = Field(default_factory=list, max_length=100)
    assumptions: list[Text] = Field(default_factory=list, max_length=100)


class Approval(Document):
    spec_sha256: Digest
    user_message: Text
    scope: Literal["integrate", "integrate_and_run"]


def review_spec(spec: ExperimentSpec) -> dict[str, Any]:
    """Identify structural omissions without claiming to understand arbitrary prose."""
    document = spec.model_dump(mode="json")
    if len(encoded(document)) > 500_000:
        raise Refused("Specification exceeds the supported size")
    missing: list[str] = []

    def require(prefix: str, fields: dict[str, Any]) -> None:
        for name, value in fields.items():
            if value is None or value == []:
                missing.append(f"{prefix}{name}")

    require("", {k: document[k] for k in ("kind", "question", "signal_rule", "observation_timing")})
    if spec.kind != "descriptive":
        require("", {k: document[k] for k in ("hypothesis", "outcome_and_horizon")})
    require(
        "data.",
        {k: v for k, v in document["data"].items() if k not in ("dataset_id", "manifest_sha256")},
    )
    require(
        "evaluation.",
        {
            k: v
            for k, v in document["evaluation"].items()
            if k != "baseline" or spec.kind != "descriptive"
        },
    )
    if spec.kind == "simulation":
        require("simulation.", (spec.simulation or SimulationNeed()).model_dump())
    elif spec.simulation is not None:
        missing.append("simulation: only a simulation experiment may specify trade rules")
    for i, component in enumerate(spec.components):
        if component.definition is None:
            missing.append(f"components[{i}].definition")
        if component.availability == "available" and component.evidence is None:
            missing.append(f"components[{i}].evidence")
    if len({c.name for c in spec.components}) != len(spec.components):
        missing.append("components: names must be unique")
    if spec.open_questions:
        missing.append("open_questions")
    if (spec.revision == 1) != (spec.supersedes_sha256 is None):
        missing.append("supersedes_sha256: required exactly when revision is greater than one")
    gaps = [
        {"component": c.name, "action": c.availability, "definition": c.definition}
        for c in spec.components
        if c.availability != "available"
    ]
    if spec.data.dataset_id is None or spec.data.manifest_sha256 is None:
        gaps.append(
            {
                "component": "dataset",
                "action": "obtain_and_admit",
                "definition": "Resolve a permitted immutable dataset identity before running",
            }
        )
    if spec.data.source_type == "external_timed_feed":
        gaps.append(
            {
                "component": "external_timed_feed",
                "action": "unsupported",
                "definition": "V1 has no external-feed/cross-clock integration",
            }
        )
    return {
        "spec_sha256": digest(encoded(document)),
        "specification": document,
        "ready_to_freeze": not missing,
        "missing_details": missing,
        "open_questions": spec.open_questions,
        "integration_work": gaps,
        "ready_to_run": False,
        "review_boundary": "Structural completeness only. The Skill must resolve semantic "
        "ambiguity and verify evidence; inventory claims are user/assistant supplied.",
        "execution_boundary": "Freezing never launches a run. General engine configuration, "
        "data admission and runtime checks remain separate.",
    }


class SpecificationStore:
    def __init__(self, settings: Settings):
        settings.validate()
        self.settings = settings
        self.root = settings.workspace / "experiments"

    def _relative(self, experiment_id: str, revision: int) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", experiment_id):
            raise Refused("Invalid experiment ID")
        if type(revision) is not int or not 1 <= revision <= 9999:
            raise Refused("Invalid experiment revision")
        return f"{experiment_id}/revision-{revision:04d}.json"

    def get(self, experiment_id: str, revision: int = 1) -> dict[str, Any]:
        relative = self._relative(experiment_id, revision)
        record = read_json(self.root, relative)
        if record.get("schema") != "frozen-experiment-spec-v1":
            raise Refused("Unsupported specification record")
        spec = ExperimentSpec.model_validate(record["specification"])
        if spec.experiment_id != experiment_id or spec.revision != revision:
            raise Refused("Specification identity mismatch")
        approval = Approval.model_validate(record["approval"])
        expected = digest(encoded(spec.model_dump(mode="json")))
        if expected != record["spec_sha256"] or approval.spec_sha256 != expected:
            raise Refused("Frozen specification digest mismatch")
        if not review_spec(spec)["ready_to_freeze"]:
            raise Refused("Frozen specification is incomplete")
        return record

    def freeze(self, spec: ExperimentSpec, approval: Approval) -> dict[str, Any]:
        review = review_spec(spec)
        if not review["ready_to_freeze"]:
            raise Refused(
                "Resolve missing details before freezing: " + ", ".join(review["missing_details"])
            )
        if approval.spec_sha256 != review["spec_sha256"]:
            raise Refused("Authorization refers to a different specification digest")
        target = self.root / self._relative(spec.experiment_id, spec.revision)
        if target.resolve() != target.absolute():
            raise Refused("Specification writes must not follow symlinks")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.root / ".freeze.lock"
        if lock_path.resolve() != lock_path.absolute():
            raise Refused("Specification lock must not follow symlinks")
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if target.exists():
                existing = self.get(spec.experiment_id, spec.revision)
                if existing["spec_sha256"] != review["spec_sha256"] or existing[
                    "approval"
                ] != approval.model_dump(mode="json"):
                    raise Refused("A frozen revision cannot be replaced; create a new revision")
                return existing
            if spec.revision > 1:
                previous = self.get(spec.experiment_id, spec.revision - 1)
                if previous["spec_sha256"] != spec.supersedes_sha256:
                    raise Refused("Previous revision digest mismatch")
            record = {
                "schema": "frozen-experiment-spec-v1",
                "status": "FROZEN_FOR_INTEGRATION",
                "spec_sha256": review["spec_sha256"],
                "specification": review["specification"],
                "approval": approval.model_dump(mode="json"),
                "frozen_unix_ns": time.time_ns(),
                "authorization_note": "Assistant-reported user instruction; not authentication "
                "or a substitute for engine access checks.",
                "integration_work_at_freeze": review["integration_work"],
                "ready_to_run": False,
            }
            atomic_json(target, record)
            return record


def inventory(settings: Settings) -> dict[str, Any]:
    """Read public registry names and default-view administrative metadata only."""
    from trading_research.data import DatasetRegistry
    from trading_research.decisions import DECISION_POLICIES
    from trading_research.evaluation import EVALUATORS, TRADE_EVALUATORS
    from trading_research.features import FEATURE_PROVIDERS
    from trading_research.models import CORPUS_RESEARCH_MODELS, RESEARCH_MODELS
    from trading_research.outcomes import OUTCOME_SPECS
    from trading_research.representations import REPRESENTATION_MODELS

    components = {
        "feature": sorted(FEATURE_PROVIDERS.names()),
        "representation": sorted(REPRESENTATION_MODELS.names()),
        "model": sorted(RESEARCH_MODELS.names()),
        "corpus_model": sorted(CORPUS_RESEARCH_MODELS.names()),
        "decision": sorted(DECISION_POLICIES.names()),
        "outcome": sorted(OUTCOME_SPECS.names()),
        "evaluator": sorted(EVALUATORS.names()),
        "trade_evaluator": sorted(TRADE_EVALUATORS.names()),
    }
    entries = (
        DatasetRegistry.discover(settings.data_roots).default_views() if settings.data_roots else ()
    )
    datasets = []
    for entry in entries:
        m = entry.manifest
        datasets.append(
            {
                "dataset_id": m.dataset_id,
                "manifest_sha256": m.manifest_sha256,
                "role": m.role.value,
                "instrument": m.symbol,
                "timeframe": m.timeframe,
                "clock_domain": m.clock_domain.identifier,
                "time_unit": m.time_unit.value,
                "columns": [column.name for column in m.columns],
            }
        )
    return {
        "components": components,
        "datasets": datasets,
        "data_status": "metadata_loaded" if settings.data_roots else "not_configured",
        "inspection": "Public registry names and manifest metadata; no market rows read",
        "boundary": "Names do not prove formula compatibility; metadata is not access permission",
        "suite_root": str(settings.suite_root),
        "workspace": str(settings.workspace),
    }
