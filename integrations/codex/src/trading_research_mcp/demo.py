"""Create new deterministic synthetic observations, never transform user market data."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from trading_research.data import DatasetRegistry, read_split_policy
from trading_research.data.manifests import read_manifest

from trading_research_mcp.service import Refused, atomic_json, digest, read_bytes


def create_demo(destination: Path, suite_root: Path) -> dict:
    """Create one external example directory; refuse an existing destination."""
    if not destination.is_absolute() or destination != destination.resolve():
        raise Refused("Demo destination must be an absolute path without symlinks")
    if destination.is_relative_to(suite_root) or suite_root.is_relative_to(destination):
        raise Refused("Keep generated demo data outside the source checkout")
    if destination.exists():
        raise Refused("Demo destination already exists; existing datasets are immutable")
    destination.mkdir(parents=True, mode=0o700)
    source = destination / "data/source"
    target = destination / "data/train"
    source.mkdir(parents=True)
    target.mkdir()
    n = 512
    steps = np.arange(n, dtype=np.float64)
    keys = 100 + np.arange(n, dtype=np.int64) * 60
    close = 100.0 + 0.01 * steps + np.sin(steps / 7.0)
    opening = close - 0.05 * np.cos(steps / 3.0)
    columns = {
        "mt5_time_raw": pa.array(keys),
        "timestamp_mt5_encoded": pa.array(keys.astype("datetime64[s]").astype("datetime64[ns]")),
        "open": pa.array(opening),
        "high": pa.array(np.maximum(opening, close) + 0.1),
        "low": pa.array(np.minimum(opening, close) - 0.1),
        "close": pa.array(close),
        "tick_volume": pa.array(np.full(n, 10, dtype=np.int64)),
        "spread": pa.array(np.zeros(n, dtype=np.int32)),
        "real_volume": pa.array(np.zeros(n, dtype=np.int64)),
    }
    partition = source / "synthetic-bars.parquet"
    pq.write_table(pa.table(columns), partition)
    partition_bytes = read_bytes(source, partition.name)
    partition_sha = digest(partition_bytes)
    first, last = int(keys[0]), int(keys[-1])
    raw = {
        "dataset_id": "SYNTHETIC_AUTHORING_SOURCE_V1",
        "dataset_builder_version": "1.0.0",
        "source": "MetaTrader5",
        "symbol": "SYNTHETIC",
        "timeframe": "M1",
        "timestamp_policy": "raw_only",
        "trade_server": "synthetic-authoring",
        "terminal_time_model": {"classification": "BROKER_WALL_CLOCK_ENCODED_AS_EPOCH"},
        "range": {"first_raw": first, "last_raw": last},
        "total_row_count": n,
        "partitions": [
            {
                "year": 1970,
                "filename": partition.name,
                "rows": n,
                "first_raw": first,
                "last_raw": last,
                "sha256": partition_sha,
                "bytes": len(partition_bytes),
            }
        ],
        "synthetic_provenance": (
            "Deterministic sine-wave fixture; source marker selects the MT5-shaped "
            "adapter. No broker export or real observations."
        ),
    }
    atomic_json(source / "manifest.json", raw)
    source_manifest = read_manifest(source / "manifest.json")
    policy_name = "synthetic-authoring-v1"
    policy = read_split_policy(suite_root / f"config/split-policies/{policy_name}.json")
    source_ref = {
        "dataset_id": source_manifest.dataset_id,
        "manifest_sha256": source_manifest.manifest_sha256,
    }
    policy_ref = {
        "adr": policy.identity.adr,
        "policy_version": policy.identity.policy_version,
        "policy_sha256": policy.identity.policy_sha256,
    }
    declared_columns = [{"name": "native_key", "dtype": "int64", "purpose": "time_key"}]
    declared_columns += [
        {"name": "timestamp_mt5_encoded", "dtype": "datetime64[ns]", "purpose": "display_only"}
    ]
    declared_columns += [
        {"name": name, "dtype": "float64", "purpose": "value"}
        for name in ("open", "high", "low", "close")
    ]
    declared_columns += [
        {"name": name, "dtype": dtype, "purpose": "value"}
        for name, dtype in (("tick_volume", "int64"), ("spread", "int32"), ("real_volume", "int64"))
    ]
    manifest = {
        "manifest_format_version": "1.1",
        "view_id": "SYNTHETIC_AUTHORING_TRAIN_V1",
        "role": "train",
        "discovery_default": True,
        "adapter_id": "mt5",
        "symbol": "SYNTHETIC",
        "timeframe": "M1",
        "clock_domain": policy.clock_domain.identifier,
        "time_unit": "seconds",
        "calendar_time_policy": "native-wallclock-v1",
        "columns": declared_columns,
        "admitted_partitions": [
            {
                "key": "synthetic-0",
                "filename": partition.name,
                "sha256": partition_sha,
                "rows": n,
                "first_key": first,
                "last_key": last,
                "bytes": len(partition_bytes),
            }
        ],
        "admitted_native_range": {"start": first, "end": last},
        "row_count_expected": n,
        "parent": source_ref,
        "partition_source": source_ref,
        "split_policy_ref": policy_ref,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "generator": {
            "name": "synthetic-authoring-demo",
            "version": "1",
            "platform_git_commit": subprocess.check_output(
                ["git", "-C", str(suite_root), "rev-parse", "HEAD"], text=True
            ).strip(),
        },
    }
    atomic_json(target / "manifest.json", manifest)
    admitted = DatasetRegistry.discover([destination / "data"]).default_views()[0].manifest
    dataset = {
        "dataset_id": admitted.dataset_id,
        "manifest_sha256": admitted.manifest_sha256,
        "role": "train",
    }
    config = {
        "schema": "research-experiment-config-v1",
        "integrity_mode": "full",
        "dataset": dataset,
        "split_policy": policy_ref,
        "bounds": {"context_start": first, "eval_start": first + 19 * 60, "eval_end": last},
        "features": [{"name": "rolling_close_position_v1", "version": 1, "params": {"window": 20}}],
        "representation": None,
        "model": {
            "name": "close_position_score_v1",
            "version": 1,
            "params": {},
        },
        "training_outcome": None,
        "evaluation_outcomes": [
            {
                "name": "forward_log_return_v1",
                "version": 1,
                "params": {
                    "boundary_policy": "nan",
                    "decision_delay_rows": 1,
                    "horizon_rows": 1,
                    "reference_price_column": "close",
                },
            }
        ],
        "policies": [
            {
                "name": "threshold_v1",
                "version": 1,
                "params": {"output_column": "score", "threshold": {"__float_hex__": "0x0.0p+0"}},
            }
        ],
        "execution": None,
        "trade_evaluators": [],
        "evaluators": [
            {
                "name": "sign_accuracy_v1",
                "version": 1,
                "params": {
                    "min_effective_samples": 1,
                    "outcome_column": "target.forward_log_return",
                    "output_column": "score",
                },
            }
        ],
        "folds": {
            "fit_rows": 64,
            "eval_rows": 32,
            "step_rows": 32,
            "embargo_rows": 0,
            "purge_override": None,
        },
        "seed": 0,
        "access_reason": None,
        "declared_lockbox_evaluation": False,
    }
    components = [
        {
            "name": "recorded-intent",
            "kind": "decision",
            "availability": "available",
            "definition": "Required engine threshold policy at score zero; "
            "records intents only, no execution or trading evaluation",
            "evidence": "decisions/reference.py: ThresholdPolicy",
        },
        {
            "name": "close-position",
            "kind": "feature",
            "availability": "implement",
            "definition": (
                "(closed close - trailing 20-observation low)/(trailing high-low) - "
                "0.5; zero for zero span; finite 19-row reach; CROSS observed gaps"
            ),
            "inputs": ["high", "low", "close"],
        },
        {
            "name": "feature-score",
            "kind": "model",
            "availability": "implement",
            "definition": (
                "Stateless output score equals the rolling_close_position feature; no fit targets"
            ),
            "inputs": ["rolling_close_position"],
        },
        {
            "name": "future-return",
            "kind": "outcome",
            "availability": "available",
            "definition": (
                "Forward log close return with one observed row decision delay and one "
                "observed row horizon; NaN at boundary"
            ),
            "evidence": "outcomes/reference.py: ForwardLogReturn",
        },
        {
            "name": "sign-measure",
            "kind": "evaluator",
            "availability": "available",
            "definition": (
                "Recorded sign accuracy of score versus forward log return, minimum one "
                "finite sample"
            ),
            "evidence": "evaluation/reference.py: SignAccuracy",
        },
    ]
    spec = {
        "experiment_id": "synthetic-close-position",
        "idea": "Exercise missing-indicator integration on synthetic observations",
        "kind": "predictive",
        "question": "Can the complete workflow measure close-position versus subsequent direction?",
        "hypothesis": (
            "Software example only: report association of close-position sign with "
            "subsequent return sign; no market-edge claim"
        ),
        "signal_rule": components[1]["definition"],
        "observation_timing": "After current observed bar closes",
        "outcome_and_horizon": components[3]["definition"],
        "data": {
            **dataset,
            "instrument": "SYNTHETIC",
            "timeframe": "M1",
            "period": "All 512 generated observations, native keys 100..30760",
            "clock_domain": policy.clock_domain.identifier,
            "time_unit": "seconds",
            "source_type": "admitted",
            "source_description": "New deterministic sine-wave software fixture; not market data",
            "required_columns": ["high", "low", "close"],
            "missing_observations": "No imputation; operate on observed rows",
        },
        "components": components,
        "evaluation": {
            "metric": "sign_accuracy_v1 and recorded sample counts",
            "baseline": "0.5 descriptive reference only; no significance claim",
            "success_criterion": (
                "Complete one canonical run and retain all fold metrics, including "
                "unfavorable or undefined values"
            ),
            "split_plan": (
                "One synthetic TRAIN view, 64 fit /32 eval /32 step; engine-derived purge"
            ),
            "stopping_rule": "One run; no tuning",
            "max_runs": 1,
            "resource_limits": (
                "One worker; 300 seconds wall/CPU, 4 GiB address space, at most 2 "
                "million admitted rows"
            ),
        },
        "assumptions": [
            (
                "This owner-authorized demonstration tests software behavior only; "
                "generated observations are explicitly synthetic"
            )
        ],
    }
    bindings = {
        "recorded-intent": ["/policies/0"],
        "close-position": ["/features/0"],
        "feature-score": ["/model"],
        "future-return": ["/evaluation_outcomes/0"],
        "sign-measure": ["/evaluators/0"],
    }
    request = {
        "experiment_id": spec["experiment_id"],
        "revision": 1,
        "config": config,
        "bindings": bindings,
        "policy_name": policy_name,
    }
    settings = {
        "suite_root": str(suite_root),
        "source_runs_root": str(destination / "historical-empty"),
        "data_roots": [str(destination / "data")],
        "workspace": str(destination / "workspace"),
        "explorer_url": None,
    }
    atomic_json(destination / "codex.json", settings)
    atomic_json(destination / "specification.json", spec)
    atomic_json(destination / "request.json", request)
    return {
        "configuration": str(destination / "codex.json"),
        "specification": str(destination / "specification.json"),
        "request": str(destination / "request.json"),
        "synthetic": True,
        "rows": n,
        "note": "No specification was frozen and no experiment was launched.",
    }
