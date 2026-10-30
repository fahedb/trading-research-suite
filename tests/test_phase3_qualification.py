"""Closed synthetic qualification matrix for the six supported Phase-3 loops."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tests.test_experiments import _case_and_runtime, _config_document, _resolved
from trading_research.experiments.orchestration import run_experiment, verify_experiment
from trading_research.experiments.runs import RunState, scan_runs
from trading_research.models.reference import StatelessRuleModel

if TYPE_CHECKING:
    from pathlib import Path

    from trading_research.core import FittedState, ModelInputs, ModelOutputFrame, ViewSpan

PHASE3_QUALIFICATION_USE_CASES = frozenset(
    {
        "cache_bypassing_verification",
        "multi_policy_fanout",
        "representation_reference_library",
        "stateless_descriptive",
        "stateless_evaluated",
        "supervised_target_channel",
    }
)


@pytest.mark.parametrize("use_case", sorted(PHASE3_QUALIFICATION_USE_CASES))
def test_all_six_supported_phase3_use_cases_complete_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_case: str,
) -> None:
    case, runtime, _base_config, audit = _case_and_runtime(tmp_path)
    document = _config_document(case)
    outcomes = cast("list[dict[str, object]]", document["evaluation_outcomes"])

    if use_case == "stateless_descriptive":
        document["evaluation_outcomes"] = []
        document["evaluators"] = []
    elif use_case == "supervised_target_channel":
        document["training_outcome"] = dict(outcomes[0])
        document["model"] = {"name": "supervised_mean_v1", "version": 1, "params": {}}
    elif use_case == "representation_reference_library":
        document["training_outcome"] = dict(outcomes[0])
        document["representation"] = {
            "name": "trailing_standardizer_v1",
            "version": 1,
            "params": {},
        }
        document["model"] = {"name": "reference_library_v1", "version": 1, "params": {}}
    elif use_case == "multi_policy_fanout":
        policies = cast("list[dict[str, object]]", document["policies"])
        policies.append({"name": "abstain_v1", "version": 1, "params": {}})
    elif use_case not in {"stateless_evaluated", "cache_bypassing_verification"}:
        raise AssertionError(f"unhandled qualification use case {use_case!r}")

    config = _resolved(tmp_path, document)
    if use_case == "cache_bypassing_verification":
        report = verify_experiment(
            config,
            runtime=runtime,
            heartbeat_interval_seconds=0.01,
        )
        assert report["passed"] is True
        assert report["cache_policy"] == "forced_ephemeral"
        records = scan_runs(
            runtime._runs_root,
            now_unix_ns=10**20,
            stale_after_ns=1,
        )
        assert len(records) == 2
        assert all(record._state is RunState.COMPLETE for record in records)
        assert not list((runtime._runs_root / "cache").rglob("*.npz"))
        assert audit.events == []
        return

    inference_calls = 0
    if use_case == "multi_policy_fanout":
        original_infer = StatelessRuleModel.infer

        def counted_infer(
            self: StatelessRuleModel,
            view: ViewSpan,
            fitted_state: FittedState,
            inputs: ModelInputs,
        ) -> ModelOutputFrame:
            nonlocal inference_calls
            inference_calls += 1
            return original_infer(self, view, fitted_state, inputs)

        monkeypatch.setattr(StatelessRuleModel, "infer", counted_infer)

    manifest = run_experiment(
        config,
        runtime=runtime,
        heartbeat_interval_seconds=0.01,
    )

    assert manifest.state is RunState.COMPLETE
    assert manifest.canonical
    assert audit.events == []
    if use_case == "stateless_descriptive":
        assert manifest.outcome_class.value == "descriptive"
        assert manifest.evaluation_count == 0
        assert len(manifest.policy_execution_json) == 3
    else:
        assert manifest.outcome_class.value == "evaluated"
        assert manifest.evaluation_count == 3
        expected_policy_executions = 6 if use_case == "multi_policy_fanout" else 3
        assert len(manifest.policy_execution_json) == expected_policy_executions
    if use_case == "multi_policy_fanout":
        assert inference_calls == 3
    artifacts = {name for name, _digest in manifest.artifact_hashes}
    assert any("representation-frame" in name for name in artifacts) is (
        use_case == "representation_reference_library"
    )
