"""Input readiness never substitutes for evaluation or creates a run."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

from tests.load_fixtures import RecordingAuditWriter, make_load_case
from tests.test_experiments import _case_and_runtime, _config_document, _resolved, _runtime_for_case
from tests.test_feature_execution import _frame, _run
from trading_research.core.errors import ContractViolationError, IntegrityError, RegistryError
from trading_research.core.identity import ViewRole
from trading_research.data import MarketFrameLoader
from trading_research.experiments.orchestration import _preflight_experiment, _register_experiment
from trading_research.features.reference import RollingClosePosition

if TYPE_CHECKING:
    from pathlib import Path


def test_preflight_checks_real_features_without_run_or_evaluation(tmp_path: Path) -> None:
    _case, runtime, config, audit = _case_and_runtime(tmp_path)
    checked = _preflight_experiment(config, runtime=runtime)
    assert checked["ready"] is True
    assert checked["full_integrity_checked"] is True
    assert checked["feature_columns"] == ["log_return_1"]
    assert checked["folds"]
    assert not (runtime._runs_root / "runs").exists()
    assert not list(runtime._runs_root.rglob("*.npz"))
    assert not audit.events


def test_missing_provider_is_reported_before_observation_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _case, runtime, config, _audit = _case_and_runtime(tmp_path)
    document = config.document()
    document["features"] = [{"name": "unimplemented", "version": 1, "params": {}}]

    def forbid(*_args: object, **_kwargs: object) -> None:
        pytest.fail("missing component must be resolved before loading market observations")

    monkeypatch.setattr(MarketFrameLoader, "load", forbid)
    with pytest.raises(RegistryError, match="unimplemented"):
        _preflight_experiment(_resolved(tmp_path, document), runtime=runtime)


def test_preflight_refuses_insufficient_feature_history(tmp_path: Path) -> None:
    _case, runtime, config, _audit = _case_and_runtime(tmp_path)
    document = config.document()
    document["features"] = [
        {"name": "rolling_close_position_v1", "version": 1, "params": {"window": 200}}
    ]
    document["model"] = {
        "name": "close_position_score_v1",
        "version": 1,
        "params": {},
    }
    with pytest.raises(ContractViolationError, match="prior observed rows"):
        _preflight_experiment(_resolved(tmp_path, document), runtime=runtime)
    assert not (runtime._runs_root / "runs").exists()


def test_full_integrity_corruption_blocks_preflight(tmp_path: Path) -> None:
    case, runtime, config, _audit = _case_and_runtime(tmp_path)
    with case.partition_paths[0].open("ab") as stream:
        stream.write(b"corrupt")
    with pytest.raises(IntegrityError):
        _preflight_experiment(config, runtime=runtime)
    assert not (runtime._runs_root / "runs").exists()


def test_preflight_refuses_protected_role_before_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = make_load_case(tmp_path, role=ViewRole.LOCKBOX)
    runtime = _runtime_for_case(case, tmp_path, audit=RecordingAuditWriter())
    document = _config_document(case)
    cast("dict[str, object]", document["dataset"])["role"] = "lockbox"
    document["declared_lockbox_evaluation"] = True
    document["access_reason"] = {"code": "synthetic-test", "detail": "test rejection"}
    document["bounds"] = {"context_start": 3100, "eval_start": 3100, "eval_end": 3202}

    def forbid(*_args: object, **_kwargs: object) -> None:
        pytest.fail("protected preflight must not read observations")

    monkeypatch.setattr(MarketFrameLoader, "load", forbid)
    with pytest.raises(ContractViolationError, match="TRAIN/VALIDATION"):
        _preflight_experiment(_resolved(tmp_path, document), runtime=runtime)


def test_row_budget_and_stale_fingerprint_refuse_before_registration(tmp_path: Path) -> None:
    _case, runtime, config, _audit = _case_and_runtime(tmp_path)
    with pytest.raises(ContractViolationError, match="row budget"):
        _preflight_experiment(config, runtime=runtime, max_rows=1)
    with pytest.raises(ContractViolationError, match="fingerprint changed"):
        _register_experiment(config, runtime=runtime, expected_fingerprint="0" * 64)
    assert not (runtime._runs_root / "runs").exists()


def test_rolling_close_position_matches_hand_computed_values_and_flat_case() -> None:
    frame = _frame((0, 1, 2, 3), high=(4, 6, 8, 10), low=(0, 2, 4, 6), close=(2, 4, 6, 8))
    result = _run(
        (RollingClosePosition(3),),
        frames=(frame,),
        eval_start=0,
        eval_end=3,
        short_context_accepted=True,
    )
    np.testing.assert_allclose(
        result.column("rolling_close_position"), [np.nan, np.nan, 0.25, 0.25]
    )
    flat = _frame((0, 1, 2), high=(5, 5, 5), low=(5, 5, 5), close=(5, 5, 5))
    result = _run(
        (RollingClosePosition(3),),
        frames=(flat,),
        eval_start=0,
        eval_end=2,
        short_context_accepted=True,
    )
    np.testing.assert_allclose(result.column("rolling_close_position"), [np.nan, np.nan, 0])


def test_worker_alarm_interrupts_a_bounded_process() -> None:
    code = (
        "from trading_research.experiments.resources import _apply_worker_limits\n"
        "import time\n"
        "_apply_worker_limits(1, None)\n"
        "try: time.sleep(10)\n"
        "except TimeoutError: print('bounded')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=5, check=True
    )
    assert completed.stdout.strip() == "bounded"
