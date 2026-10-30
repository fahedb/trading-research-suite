"""Bounded Gaps workflow over versioned engine files and its detached CLI."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from trading_research.core.identity import DatasetRef, ViewRole
from trading_research.data import (
    AccessClassification,
    DatasetRegistry,
    classify_access,
    read_split_policy,
)
from trading_research.experiments.config import load_resolved_config

STUDY = "gaps-h1-train-v1"
HISTORICAL_RUN = "01M4110M4MMQ9E73DV2ZJMAS92"
CONFIG_DIGEST = "b5c1de675ef4ee96f8fd2b0467ca355ee087c34dc4b41516419ba0057f2715b5"
TEMPLATE = "config/experiments/gap-sensitive-levels-h1-train-plane-b-v1.json"
REPORT = "docs/research/GAP_SENSITIVE_LEVELS_H1_TRAIN_V1.md"
RUN_ID = re.compile(r"[0-7][0-9A-HJKMNP-TV-Z]{25}")
PLAN_ID = re.compile(r"[0-9a-f]{32}")
LIMITATIONS = [
    "TRAIN-only, frozen Plane-B-first study; no Plane-A predictive edge established.",
    "Cost semantics and recorded-path execution are unverified; money is conditional.",
    "Fold-boundary exits can dominate this study; a rerun is not a new hypothesis.",
    "Market keys are native broker wall-clock, never assumed UTC.",
]


class Refused(ValueError):
    """An input or recorded artifact cannot support the requested operation."""


def encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_bytes(root: Path, relative: str, limit: int = 4_000_000) -> bytes:
    """Read a bounded regular file without following links below a trusted root."""
    path = root / relative
    if path.resolve() != path.absolute() or not path.is_relative_to(root):
        raise Refused("Artifact path escapes its configured root or contains a symlink")
    if not path.is_file():
        raise Refused("Required artifact is missing or is not a regular file")
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise Refused("Artifact exceeds the supported size")
    return payload


def read_json(root: Path, relative: str) -> dict[str, Any]:
    value = json.loads(read_bytes(root, relative))
    if not isinstance(value, dict):
        raise Refused("Expected a JSON object")
    return value


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    """Durably publish invocation records outside the research checkout."""
    if path.resolve() != path.absolute():
        raise Refused("Workspace writes must not follow symlinks")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded(document))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class Settings:
    suite_root: Path
    source_runs_root: Path
    data_roots: tuple[Path, ...]
    workspace: Path
    explorer_url: str | None = None

    @property
    def output_root(self) -> Path:
        return self.workspace / "engine"

    def validate(self) -> None:
        for root in (self.suite_root, self.source_runs_root, self.workspace, *self.data_roots):
            if not root.is_absolute() or root != root.resolve():
                raise Refused("Configuration requires absolute resolved paths")
        protected = (self.suite_root, self.source_runs_root, *self.data_roots)
        if any(
            self.workspace.is_relative_to(p) or p.is_relative_to(self.workspace) for p in protected
        ):
            raise Refused("Workspace must be separate from code, historical runs and data")
        if self.explorer_url:
            url = urlsplit(self.explorer_url)
            if (
                url.scheme != "http"
                or url.hostname != "127.0.0.1"
                or not url.port
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path not in ("", "/")
            ):
                raise Refused("Explorer URL must be http://127.0.0.1:PORT")

    @classmethod
    def load(cls, path: Path) -> Settings:
        value = read_json(path.parent.resolve(), path.name)
        if set(value) != {
            "suite_root",
            "source_runs_root",
            "data_roots",
            "workspace",
            "explorer_url",
        }:
            raise Refused("Unsupported workspace configuration fields")
        settings = cls(
            suite_root=Path(value["suite_root"]),
            source_runs_root=Path(value["source_runs_root"]),
            data_roots=tuple(Path(p) for p in value["data_roots"]),
            workspace=Path(value["workspace"]),
            explorer_url=value["explorer_url"],
        )
        settings.validate()
        return settings


class ResearchService:
    def __init__(self, settings: Settings):
        settings.validate()
        self.settings = settings

    def template(self) -> dict[str, Any]:
        config = load_resolved_config(self.settings.suite_root / TEMPLATE)
        if digest(config.canonical_json().encode()) != CONFIG_DIGEST:
            raise Refused("The frozen Gaps template has changed")
        return json.loads(config.canonical_json())

    def capabilities(self) -> dict[str, Any]:
        return {
            "study": STUDY,
            "transport": "local-stdio",
            "platform": "Linux",
            "historical_run_id": HISTORICAL_RUN,
            "historical_run_available": (
                self.settings.source_runs_root / "runs" / HISTORICAL_RUN / "manifest.json"
            ).is_file(),
            "scope": "General checked fold-fit experiments on TRAIN/VALIDATION; "
            "exact Gaps tools retained",
            "authoring": {
                "general_specification_review": True,
                "immutable_specification_freeze": True,
                "component_and_dataset_inventory": True,
                "skill": "research-experiment",
                "integration_requires": "Coding-capable host, suite checkout and permitted data",
                "generic_mcp_launch": True,
                "launch_boundary": "Frozen spec, explicit config binding, passed checkout checks, "
                "full input preflight, clean source identity and bounded attempts/resources",
            },
            "data_included": False,
            "personal_import": False,
            "dataset_catalog": False,
            "limitations": LIMITATIONS,
        }

    def study(self) -> dict[str, Any]:
        config = self.template()
        return {
            "study": STUDY,
            "title": "Gap Sensitive Levels H1",
            "config": config,
            "config_sha256": CONFIG_DIGEST,
            "historical_run_id": HISTORICAL_RUN,
            "historical_report": str(self.settings.suite_root / REPORT),
            "report_kind": "Historical documentation; use research_results for local evidence",
            "description": "Closed H1 gap/level events with RSI and EMA confirmation. "
            "One position, no TP/SL; opposite event or fold boundary closes it.",
            "context_rows": 120780,
            "limitations": LIMITATIONS,
        }

    def datasets(self) -> dict[str, Any]:
        config = self.template()
        registry = DatasetRegistry.discover(self.settings.data_roots)
        entry = registry.resolve(DatasetRef(**{**config["dataset"], "role": ViewRole.TRAIN}))
        policy = read_split_policy(
            self.settings.suite_root / "config/split-policies/adr-0012-v1.json"
        )
        if classify_access(entry.manifest, policy) is not AccessClassification.NORMAL:
            raise Refused("Dataset metadata requires protected access")
        return {
            "dataset": config["dataset"],
            "available": True,
            "inspection": "Manifest metadata only; no market rows read",
            "manifest_sha256": entry.identity.manifest_sha256,
        }

    def _directory(self, run_id: str) -> Path:
        if not RUN_ID.fullmatch(run_id):
            raise Refused("Expected a bare run ID")
        candidates = [
            p / "runs" / run_id for p in (self.settings.source_runs_root, self.settings.output_root)
        ]
        found = [p for p in candidates if p.is_dir()]
        if len(found) != 1:
            raise Refused("Run is missing or occurs in more than one configured root")
        if found[0].resolve() != found[0]:
            raise Refused("Run directory symlinks are not supported")
        return found[0]

    def _metadata(self, run_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        directory = self._directory(run_id)
        state = read_json(directory, "state.json")
        identity = read_json(directory, "identity.json")
        if state.get("schema") != "research-run-state-v2" or state.get("run_id") != run_id:
            raise Refused("Unsupported or mismatched run state")
        if identity.get("schema") != "research-experiment-identity-v1":
            raise Refused("Unsupported run identity")
        inputs = identity["fingerprint_inputs"]
        if digest(encoded(inputs["resolved_config"])) != CONFIG_DIGEST:
            raise Refused("Only the exact frozen TRAIN Gaps study is supported")
        if (
            inputs.get("declared_lockbox_evaluation") is not False
            or state.get("oos_touched") is not False
            or state.get("lockbox_touched") is not False
        ):
            raise Refused("Protected-data runs are not available through this adapter")
        if digest(encoded(inputs)) != identity["experiment_fingerprint"]:
            raise Refused("Experiment fingerprint mismatch")
        return directory, state, identity

    def status(self, run_id: str = HISTORICAL_RUN) -> dict[str, Any]:
        _, state, identity = self._metadata(run_id)
        return {
            "run_id": run_id,
            "state": state["state"],
            "experiment_fingerprint": identity["experiment_fingerprint"],
            "updated_unix_ns": state["updated_unix_ns"],
            "observation": "Recorded state only; no stale-run reconciliation performed",
            "source": "run files",
            "limitations": LIMITATIONS,
        }

    def explorer(self, run_id: str, fingerprint: str) -> dict[str, Any]:
        base = self.settings.explorer_url
        if not base:
            return {"available": False, "reason": "No local Explorer configured"}
        base = base.rstrip("/")
        try:
            # No proxies or redirect following: contact only the configured loopback server.
            import http.client

            parsed = urlsplit(base)
            connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=2)
            try:
                connection.request("GET", f"/api/runs/{run_id}")
                response = connection.getresponse()
                if response.status != 200:
                    raise Refused("Viewer does not serve this run")
                payload = response.read(100_001)
                if len(payload) > 100_000:
                    raise Refused("Unexpected viewer response")
                detail = json.loads(payload)
            finally:
                connection.close()
            if (
                detail.get("run_id") != run_id
                or detail.get("experiment_fingerprint") != fingerprint
                or detail.get("state") != "COMPLETE"
            ):
                raise Refused("Viewer is serving a different run identity")
        except (OSError, ValueError, http.client.HTTPException):
            return {"available": False, "reason": "Viewer unavailable or run identity mismatch"}
        return {
            "available": True,
            "overview_url": f"{base}/run?id={run_id}",
            "chart_url": f"{base}/chart?run={run_id}",
        }

    def results(self, run_id: str = HISTORICAL_RUN) -> dict[str, Any]:
        directory, state, identity = self._metadata(run_id)
        if state["state"] != "COMPLETE":
            raise Refused("Results require a COMPLETE run")
        manifest = read_json(directory, "manifest.json")
        if (
            manifest.get("schema") != "research-run-manifest-v3"
            or manifest.get("run_id") != run_id
            or manifest.get("state") != "COMPLETE"
            or manifest.get("experiment_fingerprint") != identity["experiment_fingerprint"]
            or manifest.get("platform") != identity["fingerprint_inputs"]["platform"]
        ):
            raise Refused("Run manifest identity mismatch")
        access = manifest["access"]
        if any(
            access.get(k) is not False
            for k in ("oos_touched", "lockbox_touched", "declared_lockbox_evaluation")
        ):
            raise Refused("Protected-data result refused")
        documents = {}
        for relative, section, name in [
            ("identity.json", "root", "identity.json"),
            ("config.resolved.json", "root", "config.resolved.json"),
            ("metrics/simulation-summary.json", "metrics", "simulation-summary.json"),
            ("metrics/evaluation-summary.json", "metrics", "evaluation-summary.json"),
        ]:
            payload = read_bytes(directory, relative)
            if digest(payload) != manifest["files"][section][name]:
                raise Refused(f"Recorded file hash mismatch: {relative}")
            documents[name] = json.loads(payload)
        if digest(encoded(documents["config.resolved.json"])) != CONFIG_DIGEST:
            raise Refused("Resolved configuration mismatch")
        simulation = documents["simulation-summary.json"]
        evaluation = documents["evaluation-summary.json"]
        if (
            simulation.get("schema") != "research-simulation-summary-v1"
            or evaluation.get("schema") != "research-evaluation-inventory-v2"
        ):
            raise Refused("Unsupported result format")
        folds = []
        for recorded in simulation["results"]:
            metrics = {}
            for entry in evaluation["entries"]:
                if (
                    entry["plane"] != "B"
                    or entry.get("trade_log_artifact_identity")
                    != recorded["trade_log_artifact_identity"]
                ):
                    continue
                for metric in entry["metrics"]:
                    bits = metric["value_float64_bits"]
                    value = None if bits is None else struct.unpack(">d", bytes.fromhex(bits))[0]
                    metrics[metric["name"]] = (
                        value if value is None or math.isfinite(value) else None
                    )
            folds.append(
                {
                    "fold_index": recorded["fold_index"],
                    "trade_count": recorded["num_trades"],
                    "boundary_forced_close_count": recorded["boundary_forced_close_count"],
                    "intents": recorded["counters"],
                    "metrics": metrics,
                }
            )

        def total(key: str) -> float | None:
            values = [f["metrics"][key] for f in folds if f["metrics"].get(key) is not None]
            return sum(values) if values else None

        drawdowns = [
            f["metrics"]["max_drawdown"]
            for f in folds
            if f["metrics"].get("max_drawdown") is not None
        ]
        return {
            "run_id": run_id,
            "state": "COMPLETE",
            "canonical": manifest["canonical"],
            "experiment_fingerprint": identity["experiment_fingerprint"],
            "platform": manifest["platform"],
            "dataset": self.template()["dataset"],
            "source": "Hash-checked recorded run JSON; no simulation or market-row reads",
            "folds": folds,
            "summary": {
                "trade_count": sum(f["trade_count"] for f in folds),
                "boundary_forced_close_count": sum(f["boundary_forced_close_count"] for f in folds),
                "conditional_net_pnl_sum": total("net_pnl"),
                "worst_fold_drawdown": max(drawdowns, default=None),
            },
            "aggregation": "Money summed over disjoint folds; drawdown is worst fold, not combined",
            "execution": manifest["simulation"],
            "limitations": LIMITATIONS,
            "explorer": self.explorer(run_id, identity["experiment_fingerprint"]),
        }

    def _evidence(self) -> dict[str, Any]:
        config = self.template()
        self.datasets()
        policy_path = self.settings.suite_root / "config/split-policies/adr-0012-v1.json"
        policy = read_split_policy(policy_path)
        if policy.identity.policy_sha256 != config["split_policy"]["policy_sha256"]:
            raise Refused("Split policy mismatch")

        def git(*args: str) -> str:
            return subprocess.check_output(
                ["git", "-C", str(self.settings.suite_root), *args],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=15,
            ).strip()

        if git("status", "--porcelain", "--untracked-files=all"):
            raise Refused("A clean recorded Git checkout is required to prepare or start")
        return {
            "config_sha256": CONFIG_DIGEST,
            "git_commit": git("rev-parse", "HEAD"),
            "lock_sha256": digest(read_bytes(self.settings.suite_root, "uv.lock")),
            "split_policy_sha256": policy.identity.policy_sha256,
            "dataset": config["dataset"],
            "suite_root": str(self.settings.suite_root),
            "data_roots": [str(p) for p in self.settings.data_roots],
            "output_root": str(self.settings.output_root),
        }

    def prepare(self) -> dict[str, Any]:
        evidence = self._evidence()
        plans = self.settings.workspace / "plans"
        plans.mkdir(parents=True, exist_ok=True, mode=0o700)
        plan_id = uuid.uuid4().hex
        plan = {
            "plan_id": plan_id,
            "evidence": evidence,
            "preflight": "Config, manifest metadata, split policy and Git checked. "
            "The engine performs full data integrity/access checks on execution.",
            "limitations": LIMITATIONS,
        }
        atomic_json(plans / f"{plan_id}.json", plan)
        return plan

    def start(self, plan_id: str) -> dict[str, Any]:
        if not PLAN_ID.fullmatch(plan_id):
            raise Refused("Invalid plan ID")
        plans = self.settings.workspace / "plans"
        plan = read_json(plans, f"{plan_id}.json")
        launches = self.settings.workspace / "launches"
        launches.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.settings.workspace / "launch.lock"
        if lock_path.resolve() != lock_path.absolute():
            raise Refused("Workspace lock must not follow symlinks")
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            receipt = launches / f"{plan_id}.json"
            if receipt.exists():
                return read_json(launches, receipt.name)
            for path in launches.glob("*.json"):
                previous = read_json(launches, path.name)
                if previous["launch_state"] != "launched":
                    raise Refused("An uncertain launch needs operator inspection")
                if previous.get("workflow") == "experiment":
                    from trading_research_mcp.execution import recorded_state

                    previous_state = recorded_state(self.settings, previous["run_id"])["state"]
                else:
                    previous_state = self.status(previous["run_id"])["state"]
                if previous_state not in (
                    "COMPLETE",
                    "FAILED",
                    "INTERRUPTED",
                ):
                    raise Refused("An adapter-launched run is still active")
            if plan["evidence"] != self._evidence():
                raise Refused("Prepared inputs changed; prepare a new plan")
            if not (shutil.which("systemd-run") or shutil.which("tmux")):
                raise Refused("No supported detached launcher installed")
            executable = self.settings.suite_root / ".venv/bin/trading-research"
            if not executable.is_file():
                raise Refused("Install the locked engine environment first: uv sync --locked")
            argv = [
                str(executable),
                "run",
                str(self.settings.suite_root / TEMPLATE),
                "--runs-root",
                str(self.settings.output_root),
                "--repository-root",
                str(self.settings.suite_root),
                "--dependency-lock",
                str(self.settings.suite_root / "uv.lock"),
                "--split-policy",
                str(self.settings.suite_root / "config/split-policies/adr-0012-v1.json"),
                "--audit",
                str(self.settings.output_root / "audit.jsonl"),
                "--json",
            ]
            for root in self.settings.data_roots:
                argv.extend(["--data-root", str(root)])
            record = {
                "plan_id": plan_id,
                "launch_state": "uncertain",
                "message": "Launch reserved; never retry automatically if interrupted",
            }
            atomic_json(receipt, record)
            try:
                completed = subprocess.run(
                    argv,
                    cwd=self.settings.suite_root,
                    text=True,
                    capture_output=True,
                    timeout=60,
                    check=True,
                )
                output = json.loads(completed.stdout)
                if not RUN_ID.fullmatch(output["run_id"]):
                    raise Refused("Engine returned an invalid run ID")
                record = {"plan_id": plan_id, "launch_state": "launched", **output}
            except (subprocess.SubprocessError, ValueError, KeyError):
                record["message"] = (
                    "Launch outcome uncertain. Inspect engine runs and this receipt; "
                    "do not retry or delete the receipt automatically."
                )
            atomic_json(receipt, record)
            return record
