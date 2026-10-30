"""Explicit full-axis published-TRAIN Phase-4 simulation qualification."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import TYPE_CHECKING

import numpy as np
import pytest

from tests.performance._measure_child import (
    POLICY_PATH,
    REGISTRY_ROOTS,
    TRAIN_EXPECTED_ROWS,
    TRAIN_MANIFEST,
    TRAIN_REF,
    _prepare_train_access,
)
from trading_research.core import (
    CommissionBasis,
    CostSemanticsStatus,
    ExecutionCostBinding,
    ExecutionPriceModel,
    ExecutionSpec,
    NativeTimeKey,
    StageOrigin,
    ViewSpan,
)
from trading_research.core.research_frames import _make_intent_frame
from trading_research.core.simulation import (
    _execution_spec_params_json,
    _make_simulation_request,
)
from trading_research.data import DatasetRegistry, MarketFrameLoader
from trading_research.data.gating import read_split_policy
from trading_research.evaluation.trade_reference import EquityDrawdown, TradeSummary
from trading_research.simulation import simulate_bracket_single_position_v1

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from trading_research.core import EquityCurve, IntentFrame, TradeLog
    from trading_research.core.frames import MarketFrame

pytestmark = pytest.mark.performance

_REPO_ROOT = Path(__file__).parents[2]
_RUNNING_FILE = Path(__file__).resolve()
_RUNS_ROOT = Path("/path/to/research-runs")
_AUDIT_PATH = _RUNS_ROOT / "audit.jsonl"
_EXPECTED_AUDIT_RECORDS = 3
_EXPECTED_AUDIT_SHA256 = "deeb73ed8784cb021f9b246fbbfe2f7ec79ef176ee7b1b42ee5596ff0e2301f5"
_TIMED_REPETITIONS = 5
_BUDGET_SECONDS = 60.0
_EVIDENCE_SCHEMA = "phase4-full-axis-simulation-performance-v1"
_POLICY_ORIGIN = StageOrigin("phase4_performance_dense_long_v1", 1, {})
_TRADE_COLUMNS = (
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
)


@dataclass(frozen=True, slots=True)
class _TimingSummary:
    samples_seconds: tuple[float, ...]
    median_seconds: float
    minimum_seconds: float


@dataclass(frozen=True, slots=True)
class _SimulationEvidence:
    elapsed_seconds: float
    execution_rows: int
    intent_rows: int
    trade_count: int
    ambiguous_exit_rate: float
    gapped_exit_rate: float
    boundary_forced_close_rate: float
    max_drawdown: float
    total_cost: float
    artifact_content_hashes: tuple[tuple[str, str], ...]
    artifact_retained_bytes: tuple[tuple[str, int], ...]


@pytest.fixture(scope="module", autouse=True)
def _require_published_train() -> None:
    assert sys.platform == "linux", "Phase 4 peak-RSS qualification requires Linux"
    assert TRAIN_MANIFEST.is_file()
    assert POLICY_PATH.is_file()
    assert _RUNS_ROOT.is_dir()
    assert _AUDIT_PATH.is_file()


def _git_output(*arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout


def _clean_committed_head() -> str:
    head = _git_output("rev-parse", "HEAD").decode("ascii").strip()
    if _git_output("status", "--porcelain=v1", "--untracked-files=all"):
        raise AssertionError("Phase 4 qualification requires a clean repository worktree")
    relative = _RUNNING_FILE.relative_to(_REPO_ROOT).as_posix()
    if _git_output("show", f"{head}:{relative}") != _RUNNING_FILE.read_bytes():
        raise AssertionError("Phase 4 performance harness is not the committed HEAD version")
    return head


def _audit_snapshot() -> bytes:
    if _AUDIT_PATH.is_symlink() or not _AUDIT_PATH.is_file():
        raise AssertionError("audit log is not one existing regular file")
    payload = _AUDIT_PATH.read_bytes()
    if len(payload.splitlines()) != _EXPECTED_AUDIT_RECORDS:
        raise AssertionError("Phase 4 qualification requires the exact three-record audit log")
    if hashlib.sha256(payload).hexdigest() != _EXPECTED_AUDIT_SHA256:
        raise AssertionError("Phase 4 qualification audit bytes differ from the baseline")
    return payload


def _peak_rss_bytes() -> int:
    import resource

    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def _execution_spec() -> ExecutionSpec:
    return ExecutionSpec(
        execution_id="bracket_single_position_v1",
        version=1,
        lot_size=0.01,
        take_profit_pips=100.0,
        stop_loss_pips=100.0,
        cost_binding=ExecutionCostBinding(
            binding_id="unverified-audit-input-v1",
            binding_digest=None,
            cost_semantics=CostSemanticsStatus.UNVERIFIED,
            execution_price_model=ExecutionPriceModel.RECORDED_PATH_UNVERIFIED,
            contract_size=100.0,
            price_units_per_pip=0.01,
            account_currency_per_quote_currency=1.0,
            spread_pips=20.0,
            commission_per_lot=6.0,
            commission_basis=CommissionBasis.PER_ROUND_TURN,
        ),
    )


def _load_train_ohlc() -> MarketFrame:
    grant = _prepare_train_access()
    policy = read_split_policy(POLICY_PATH)
    loader = MarketFrameLoader(
        registry=DatasetRegistry.discover(REGISTRY_ROOTS),
        split_policy=policy,
    )
    frame = loader.load(grant, columns=("open", "high", "low", "close"))
    if frame.dataset_ref != TRAIN_REF or frame.num_rows != TRAIN_EXPECTED_ROWS:
        raise AssertionError("published TRAIN OHLC load returned an unexpected identity or axis")
    if frame.column_names != ("open", "high", "low", "close"):
        raise AssertionError("published TRAIN OHLC load returned an unexpected schema")
    return frame


def _full_view(frame: MarketFrame) -> ViewSpan:
    keys = frame.keys
    start = NativeTimeKey(int(keys[0]), frame.unit, frame.clock_domain)
    end = NativeTimeKey(int(keys[-1]), frame.unit, frame.clock_domain)
    return ViewSpan(
        (frame.dataset_ref.canonical_identity,),
        frame.unit,
        frame.clock_domain,
        start,
        start,
        end,
    )


def _bundle_evidence(
    arrays: Mapping[str, np.ndarray[tuple[int], np.dtype[np.generic]]],
) -> tuple[str, int]:
    digest = hashlib.sha256()
    retained = 0
    for name in sorted(arrays):
        values = arrays[name]
        digest.update(name.encode("ascii"))
        digest.update(values.dtype.str.encode("ascii"))
        digest.update(str(values.shape).encode("ascii"))
        digest.update(values.tobytes(order="C"))
        retained += int(values.nbytes)
    return digest.hexdigest(), retained


def _artifact_evidence(
    intent: IntentFrame,
    trades: TradeLog,
    equity: EquityCurve,
) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, int], ...]]:
    bundles = {
        "intent": {"keys": intent.keys, "target_exposure": intent.target_exposure},
        "trades": {name: getattr(trades, name) for name in _TRADE_COLUMNS},
        "equity": {"keys": equity.keys, "equity": equity.equity},
    }
    observed = {name: _bundle_evidence(arrays) for name, arrays in bundles.items()}
    return (
        tuple((name, values[0]) for name, values in sorted(observed.items())),
        tuple((name, values[1]) for name, values in sorted(observed.items())),
    )


def _run_once(spec: ExecutionSpec) -> _SimulationEvidence:
    started = perf_counter()
    frame = _load_train_ohlc()
    request = _make_simulation_request(
        keys=frame.keys,
        unit=frame.unit,
        clock_domain=frame.clock_domain,
        source_identities=(frame.dataset_ref.canonical_identity,),
        open_values=frame.column("open"),
        high_values=frame.column("high"),
        low_values=frame.column("low"),
        close_values=frame.column("close"),
    )
    dense_target = np.ones(request.num_rows, dtype=np.float64)
    intent = _make_intent_frame(
        view=_full_view(frame),
        intent_schema_id="target_exposure_intent_v1",
        keys=request.keys,
        target_exposure=dense_target,
    )
    trades, equity = simulate_bracket_single_position_v1(
        request,
        intent,
        spec,
        fold_index=0,
        policy_origin=_POLICY_ORIGIN,
    )
    summary = TradeSummary(min_effective_trades=1).evaluate(trades, equity)
    drawdown = EquityDrawdown(min_effective_rows=1).evaluate(trades, equity)
    content_hashes, retained_bytes = _artifact_evidence(intent, trades, equity)

    if request.num_rows != TRAIN_EXPECTED_ROWS or intent.num_rows != TRAIN_EXPECTED_ROWS:
        raise AssertionError("qualification did not simulate the complete dense TRAIN axis")
    if trades.execution_rows != TRAIN_EXPECTED_ROWS or equity.num_rows != TRAIN_EXPECTED_ROWS:
        raise AssertionError("simulation artifacts do not cover the complete TRAIN axis")
    if trades.num_trades <= 0:
        raise AssertionError("configured brackets did not produce a completed trade")
    if trades.intent_rows_long != TRAIN_EXPECTED_ROWS:
        raise AssertionError("qualification intent was not dense LONG over every row")
    if trades.unexecuted_final_row_intents != 1:
        raise AssertionError("final dense intent was not bounded at eval_end")
    if spec.cost_binding.cost_semantics is not CostSemanticsStatus.UNVERIFIED:
        raise AssertionError("TRAIN qualification lost its permanent UNVERIFIED marking")
    if json.loads(_execution_spec_params_json(spec))["cost_binding"]["binding_digest"] is not None:
        raise AssertionError("unverified qualification unexpectedly acquired a binding digest")
    rates = tuple(
        summary[name].value
        for name in (
            "ambiguous_exit_rate",
            "gapped_exit_rate",
            "boundary_forced_close_rate",
        )
    )
    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in rates):
        raise AssertionError("execution-honesty rates are not finite probabilities")
    elapsed = perf_counter() - started
    return _SimulationEvidence(
        elapsed_seconds=elapsed,
        execution_rows=request.num_rows,
        intent_rows=intent.num_rows,
        trade_count=trades.num_trades,
        ambiguous_exit_rate=rates[0],
        gapped_exit_rate=rates[1],
        boundary_forced_close_rate=rates[2],
        max_drawdown=drawdown["max_drawdown"].value,
        total_cost=summary["total_cost"].value,
        artifact_content_hashes=content_hashes,
        artifact_retained_bytes=retained_bytes,
    )


def _summarize(samples: Sequence[float]) -> _TimingSummary:
    observed = tuple(samples)
    if len(observed) != _TIMED_REPETITIONS or any(
        not math.isfinite(value) or value < 0.0 for value in observed
    ):
        raise AssertionError("qualification requires exactly five finite non-negative samples")
    return _TimingSummary(observed, median(observed), min(observed))


def _deterministic_signature(evidence: _SimulationEvidence) -> tuple[object, ...]:
    return (
        evidence.execution_rows,
        evidence.intent_rows,
        evidence.trade_count,
        evidence.ambiguous_exit_rate,
        evidence.gapped_exit_rate,
        evidence.boundary_forced_close_rate,
        evidence.max_drawdown,
        evidence.total_cost,
        evidence.artifact_content_hashes,
        evidence.artifact_retained_bytes,
    )


def _render_evidence(document: Mapping[str, object]) -> None:
    print("PHASE4_SIMULATION_EVIDENCE_BEGIN")
    print(json.dumps(document, sort_keys=True, allow_nan=False, separators=(",", ":")))
    print("PHASE4_SIMULATION_EVIDENCE_END")


def test_published_train_full_axis_simulation_meets_budget(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run one discarded warm-up and five full-axis dense TRAIN simulations."""
    audit_before = _audit_snapshot()
    head = _clean_committed_head()
    spec = _execution_spec()
    pre_measure_rss_bytes = _peak_rss_bytes()

    warmup = _run_once(spec)
    measured = tuple(_run_once(spec) for _ in range(_TIMED_REPETITIONS))
    expected_signature = _deterministic_signature(warmup)
    assert all(_deterministic_signature(item) == expected_signature for item in measured)
    timing = _summarize(tuple(item.elapsed_seconds for item in measured))
    audit_after = _audit_snapshot()
    assert audit_after == audit_before
    peak_rss_bytes = _peak_rss_bytes()
    assert peak_rss_bytes >= pre_measure_rss_bytes > 0

    evidence: dict[str, object] = {
        "schema": _EVIDENCE_SCHEMA,
        "target_dataset_id": TRAIN_REF.dataset_id,
        "target_manifest_sha256": TRAIN_REF.manifest_sha256,
        "target_role": TRAIN_REF.role.value,
        "dataset_row_count": TRAIN_EXPECTED_ROWS,
        "execution_rows": warmup.execution_rows,
        "intent_rows": warmup.intent_rows,
        "trade_count": warmup.trade_count,
        "ambiguous_exit_rate": warmup.ambiguous_exit_rate,
        "gapped_exit_rate": warmup.gapped_exit_rate,
        "boundary_forced_close_rate": warmup.boundary_forced_close_rate,
        "max_drawdown_unverified": warmup.max_drawdown,
        "total_cost_unverified": warmup.total_cost,
        "cost_semantics": "unverified",
        "execution_price_model": "recorded_path_unverified",
        "binding_id": "unverified-audit-input-v1",
        "binding_digest": None,
        "artifact_content_hashes": dict(warmup.artifact_content_hashes),
        "artifact_retained_bytes": dict(warmup.artifact_retained_bytes),
        "git_commit": head,
        "pre_measure_rss_bytes": pre_measure_rss_bytes,
        "peak_rss_bytes": peak_rss_bytes,
        "audit_record_count_before": len(audit_before.splitlines()),
        "audit_record_count_after": len(audit_after.splitlines()),
        "audit_sha256_before": hashlib.sha256(audit_before).hexdigest(),
        "audit_sha256_after": hashlib.sha256(audit_after).hexdigest(),
        "budget_seconds": _BUDGET_SECONDS,
        "samples_seconds": list(timing.samples_seconds),
        "median_seconds": timing.median_seconds,
        "minimum_seconds": timing.minimum_seconds,
    }
    with capsys.disabled():
        _render_evidence(evidence)

    assert timing.median_seconds < _BUDGET_SECONDS, (
        f"full-axis TRAIN simulation median {timing.median_seconds:.9f}s missed "
        f"{_BUDGET_SECONDS:.1f}s budget; samples={timing.samples_seconds!r}"
    )
