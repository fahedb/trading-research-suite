"""A complete design is distinct from supported components or permission to run."""

import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import ValidationError

from trading_research_mcp.service import Refused, Settings, atomic_json
from trading_research_mcp.specification import (
    Approval,
    ExperimentSpec,
    SpecificationStore,
    inventory,
    review_spec,
)


@pytest.fixture
def spec():
    return ExperimentSpec.model_validate(
        {
            "experiment_id": "range-return",
            "idea": "Test whether trailing range predicts returns",
            "kind": "predictive",
            "question": "Does the last 20-bar range predict the next return?",
            "hypothesis": "A wider past range predicts a larger absolute next-bar return",
            "signal_rule": "Trailing max(high,20) minus min(low,20) over observed rows",
            "observation_timing": "After bar close; only past and current observations",
            "outcome_and_horizon": "Absolute log close-to-close return one observed row ahead",
            "data": {
                "instrument": "TEST",
                "timeframe": "M1",
                "period": "Entire admitted TRAIN range",
                "clock_domain": "synthetic.example.wallclock",
                "time_unit": "seconds",
                "role": "train",
                "source_type": "personal_file",
                "source_description": "A synthetic test input",
                "required_columns": ["high", "low", "close"],
                "missing_observations": "Preserve observed rows; no invented candles",
            },
            "components": [
                {
                    "name": "trailing-range",
                    "kind": "feature",
                    "availability": "implement",
                    "definition": "20 observed rows, finite reach 19, CROSS gaps, float64",
                    "inputs": ["high", "low"],
                }
            ],
            "evaluation": {
                "metric": "Mean absolute error of predicted absolute return",
                "baseline": "Mean absolute return fitted on permitted fit rows only",
                "success_criterion": "Report baseline-relative error and effective sample counts",
                "split_plan": "Sequential folds inside TRAIN, engine-derived purge",
                "stopping_rule": "One preregistered run; report even empty or unfavorable results",
                "max_runs": 1,
                "resource_limits": "At most 10 minutes and 2 GB in one worker",
            },
        }
    )


@pytest.fixture
def store(tmp_path):
    return SpecificationStore(
        Settings(tmp_path / "suite", tmp_path / "runs", (), tmp_path / "workspace")
    )


def approval(spec, scope="integrate"):
    return Approval(
        spec_sha256=review_spec(spec)["spec_sha256"],
        user_message="Test fixture authorization for this exact specification",
        scope=scope,
    )


def test_partial_description_cannot_freeze_or_create_run(store):
    draft = ExperimentSpec(experiment_id="rsi", idea="Buy below RSI 30")
    result = review_spec(draft)
    assert not result["ready_to_freeze"]
    assert "data.clock_domain" in result["missing_details"]
    assert "kind" in result["missing_details"]
    with pytest.raises(Refused, match="Resolve missing"):
        store.freeze(draft, approval(draft))
    assert not store.settings.workspace.exists()


def test_missing_implementation_and_data_are_tasks_not_undefined_semantics(spec):
    result = review_spec(spec)
    assert result["ready_to_freeze"]
    assert not result["ready_to_run"]
    assert {item["action"] for item in result["integration_work"]} == {
        "implement",
        "obtain_and_admit",
    }
    assert spec.simulation is None


def test_simulation_requires_exits_costs_and_methodology(spec):
    doc = spec.model_dump()
    doc["kind"] = "simulation"
    result = review_spec(ExperimentSpec.model_validate(doc))
    assert not result["ready_to_freeze"]
    assert "simulation.exit_rule" in result["missing_details"]
    assert "simulation.costs" in result["missing_details"]
    assert "simulation.methodology_basis" in result["missing_details"]


def test_descriptive_study_does_not_need_future_outcome_or_trades(spec):
    doc = spec.model_dump()
    doc.update(kind="descriptive", hypothesis=None, outcome_and_horizon=None)
    doc["evaluation"]["baseline"] = None
    result = review_spec(ExperimentSpec.model_validate(doc))
    assert result["ready_to_freeze"]


def test_open_questions_unknown_formulas_and_unproven_reuse_block_freeze(spec):
    doc = spec.model_dump()
    doc["open_questions"] = ["What does a gap mean here?"]
    doc["components"][0].update(definition=None, availability="available", evidence=None)
    result = review_spec(ExperimentSpec.model_validate(doc))
    assert {"open_questions", "components[0].definition", "components[0].evidence"}.issubset(
        result["missing_details"]
    )


def test_unsupported_external_feed_remains_visible(spec):
    doc = spec.model_dump()
    doc["data"]["source_type"] = "external_timed_feed"
    result = review_spec(ExperimentSpec.model_validate(doc))
    assert result["ready_to_freeze"]
    assert any(item["action"] == "unsupported" for item in result["integration_work"])
    assert not result["ready_to_run"]


def test_freeze_requires_authorization_of_exact_digest_and_preserves_scope(spec, store):
    wrong = approval(spec).model_copy(update={"spec_sha256": "0" * 64})
    with pytest.raises(Refused, match="different specification"):
        store.freeze(spec, wrong)
    assert not store.settings.workspace.exists()
    frozen = store.freeze(spec, approval(spec))
    assert frozen["status"] == "FROZEN_FOR_INTEGRATION"
    assert frozen["approval"]["scope"] == "integrate"
    assert not store.settings.output_root.exists()
    assert not frozen["ready_to_run"]


def test_concurrent_identical_freezes_return_one_record(spec, store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda _: store.freeze(spec, approval(spec)), range(4)))
    assert all(record == records[0] for record in records)
    assert len(list(store.root.rglob("revision-*.json"))) == 1


def test_frozen_semantics_and_approval_cannot_be_replaced(spec, store):
    first = store.freeze(spec, approval(spec))
    changed = spec.model_copy(update={"hypothesis": "Changed hypothesis"})
    with pytest.raises(Refused, match="cannot be replaced"):
        store.freeze(changed, approval(changed))
    with pytest.raises(Refused, match="cannot be replaced"):
        store.freeze(spec, approval(spec, "integrate_and_run"))
    assert store.get(spec.experiment_id) == first


def test_new_revision_requires_the_actual_predecessor(spec, store):
    first = store.freeze(spec, approval(spec))
    next_spec = spec.model_copy(update={"revision": 2, "supersedes_sha256": "0" * 64})
    with pytest.raises(Refused, match="Previous revision"):
        store.freeze(next_spec, approval(next_spec))
    next_spec = next_spec.model_copy(
        update={
            "supersedes_sha256": first["spec_sha256"],
            "hypothesis": "A different, explicitly revised hypothesis",
        }
    )
    second = store.freeze(next_spec, approval(next_spec))
    assert store.get(spec.experiment_id, 1) == first
    assert store.get(spec.experiment_id, 2) == second


def test_modified_frozen_spec_is_refused(spec, store):
    record = store.freeze(spec, approval(spec))
    path = store.root / spec.experiment_id / "revision-0001.json"
    record["specification"]["hypothesis"] = "Altered after freeze"
    path.write_text(json.dumps(record))
    with pytest.raises(Refused, match="digest mismatch"):
        store.get(spec.experiment_id)


def test_path_escape_and_symlink_refused(spec, store, tmp_path):
    with pytest.raises(Refused, match="Invalid experiment"):
        store.get("../../escape")
    store.settings.workspace.mkdir()
    store.root.symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(Refused, match="symlinks"):
        store.freeze(spec, approval(spec))
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize(
    "edit",
    [
        {"revision": True},
        {"experiment_id": "../../escape"},
        {"unexpected": "value"},
        {"question": "  "},
        {"data": {"role": "lockbox"}},
    ],
)
def test_invalid_documents_do_not_silently_pass(spec, edit):
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({**spec.model_dump(), **edit})


def test_empty_workspace_inventory_contains_component_names_only(store):
    result = inventory(store.settings)
    assert "log_return_1" in result["components"]["feature"]
    assert result["datasets"] == []
    assert result["data_status"] == "not_configured"
    assert not store.settings.workspace.exists()


def test_stdio_review_freeze_resume_and_gaps_launch_boundary(spec, store, tmp_path):
    config = tmp_path / "codex.json"
    atomic_json(
        config,
        {
            "suite_root": str(store.settings.suite_root),
            "source_runs_root": str(store.settings.source_runs_root),
            "data_roots": [],
            "workspace": str(store.settings.workspace),
            "explorer_url": None,
        },
    )

    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "trading_research_mcp.server", "--config", str(config)],
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}
            assert tools["experiment_review"].annotations.readOnlyHint
            assert tools["experiment_get"].annotations.readOnlyHint
            assert not tools["experiment_freeze"].annotations.readOnlyHint
            reviewed = await session.call_tool("experiment_review", {"spec": spec.model_dump()})
            assert not reviewed.isError
            assert reviewed.structuredContent["ready_to_freeze"]
            frozen = await session.call_tool(
                "experiment_freeze",
                {"spec": spec.model_dump(), "approval": approval(spec).model_dump()},
            )
            assert not frozen.isError
            restored = await session.call_tool(
                "experiment_get", {"experiment_id": spec.experiment_id}
            )
            assert restored.structuredContent == frozen.structuredContent
            invalid_start = await session.call_tool(
                "research_start", {"plan_id": frozen.structuredContent["spec_sha256"]}
            )
            assert invalid_start.isError

    asyncio.run(exercise())
    assert not store.settings.output_root.exists()
