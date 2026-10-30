"""Prepared general launches over the engine CLI; no alternative research runtime."""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import shutil
import struct
import subprocess
import uuid
from contextlib import contextmanager
from typing import Any

from trading_research.core.errors import TradingResearchError
from trading_research.core.identity import DatasetRef, ViewRole
from trading_research.data import (
    AccessClassification,
    DatasetRegistry,
    classify_access,
    read_split_policy,
)
from trading_research.experiments.config import load_resolved_config

from trading_research_mcp.service import (
    PLAN_ID,
    RUN_ID,
    Refused,
    ResearchService,
    Settings,
    atomic_json,
    digest,
    encoded,
    read_bytes,
    read_json,
)
from trading_research_mcp.specification import ExperimentSpec, SpecificationStore

LIMITS = {"max_rows": 2_000_000, "max_runtime_seconds": 300, "max_memory_bytes": 4_294_967_296}
TERMINAL = {"COMPLETE", "FAILED", "INTERRUPTED"}


def recorded_state(settings: Settings, run_id: str) -> dict[str, Any]:
    if not RUN_ID.fullmatch(run_id):
        raise Refused("Invalid run ID")
    state = read_json(settings.output_root / "runs" / run_id, "state.json")
    if state.get("schema") != "research-run-state-v2" or state.get("run_id") != run_id:
        raise Refused("Run state identity mismatch")
    if state.get("state") not in TERMINAL | {"PENDING", "RUNNING"}:
        raise Refused("Unknown run state")
    return state


class ExperimentService:
    def __init__(self, settings: Settings):
        settings.validate()
        self.settings = settings
        self.specs = SpecificationStore(settings)

    def _mkdir(self, relative: str):
        path = self.settings.workspace / relative
        if path.resolve() != path.absolute():
            raise Refused("Workspace operations must not follow symlinks")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    @contextmanager
    def _lock(self, name: str):
        root = self._mkdir("")
        path = root / name
        if path.resolve() != path.absolute():
            raise Refused("Lock must not follow symlinks")
        with path.open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def _checkout(self, *, require_clean: bool = True) -> dict[str, Any]:
        root = self.settings.suite_root

        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(root), *args], text=True, timeout=15
            ).strip()

        dirty = bool(git("status", "--porcelain", "--untracked-files=all"))
        if dirty and require_clean:
            raise Refused("A clean recorded checkout is required")
        if not (root / ".venv/bin/trading-research").is_file():
            raise Refused("Install the locked engine environment first")
        installed = subprocess.check_output(
            [
                str(root / ".venv/bin/python"),
                "-c",
                "import pathlib,trading_research;"
                "print(pathlib.Path(trading_research.__file__).resolve().parent)",
            ],
            text=True,
            timeout=15,
        ).strip()
        if installed != str(root / "src/trading_research"):
            raise Refused("Engine environment points to a different source checkout")
        return {
            "git_commit": git("rev-parse", "HEAD"),
            "dirty": dirty,
            "lock_sha256": digest(read_bytes(root, "uv.lock")),
            "adapter_lock_sha256": digest(read_bytes(root, "integrations/codex/uv.lock")),
            "suite_root": str(root),
        }

    def verify_checkout(self) -> dict[str, Any]:
        """Actually run fixed checks; cache successful evidence for this clean commit."""
        with self._lock("verify.lock"):
            identity = self._checkout()
            key = digest(encoded(identity))
            directory = self._mkdir("verification")
            receipt = directory / f"{key}.json"
            if receipt.exists():
                return self._verification(identity)
            root = self.settings.suite_root
            commands = [
                ([str(root / "scripts/gate.sh")], root),
                (
                    [str(root / "integrations/codex/.venv/bin/python"), "-m", "pytest"],
                    root / "integrations/codex",
                ),
                (
                    [str(root / "integrations/codex/.venv/bin/ruff"), "check", "src", "tests"],
                    root / "integrations/codex",
                ),
            ]
            log_path = directory / f"{key}-{uuid.uuid4().hex}.log"
            checks = []
            with log_path.open("xb") as log:
                for command, cwd in commands:
                    completed = subprocess.run(
                        command, cwd=cwd, stdout=log, stderr=log, timeout=900
                    )
                    checks.append({"command": command, "exit_code": completed.returncode})
                    if completed.returncode:
                        return {"passed": False, "checks": checks, "log": str(log_path)}
            if self._checkout() != identity:
                raise Refused("Checkout changed while checks ran; repeat verification")
            record = {
                "schema": "experiment-checkout-verification-v1",
                "passed": True,
                "checkout": identity,
                "checks": checks,
                "log_name": log_path.name,
                "log_sha256": digest(read_bytes(directory, log_path.name)),
            }
            atomic_json(receipt, record)
            return record

    def _verification(self, identity: dict[str, Any]) -> dict[str, Any]:
        directory = self.settings.workspace / "verification"
        record = read_json(directory, digest(encoded(identity)) + ".json")
        if (
            record.get("schema") != "experiment-checkout-verification-v1"
            or record.get("passed") is not True
            or record.get("checkout") != identity
        ):
            raise Refused("Passing checkout verification is missing or stale")
        if len(record.get("checks", [])) != 3 or any(
            c.get("exit_code") != 0 for c in record["checks"]
        ):
            raise Refused("Required checkout checks did not all pass")
        if digest(read_bytes(directory, record["log_name"])) != record["log_sha256"]:
            raise Refused("Checkout verification log changed")
        return record

    def _binding(self, frozen, config, bindings, policy_name):
        spec = ExperimentSpec.model_validate(frozen["specification"])
        if spec.data.source_type == "external_timed_feed" or any(
            c.availability == "unsupported" for c in spec.components
        ):
            raise Refused("Specification includes an unsupported dependency")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", policy_name):
            raise Refused("Invalid split policy name")
        policy_relative = f"config/split-policies/{policy_name}.json"
        read_bytes(self.settings.suite_root, policy_relative)
        policy = read_split_policy(self.settings.suite_root / policy_relative)
        if (
            config.get("integrity_mode") != "full"
            or config.get("declared_lockbox_evaluation") is not False
            or config.get("access_reason") is not None
        ):
            raise Refused(
                "Only full-integrity experiments without protected-data access are supported"
            )
        if config.get("schema") != "research-experiment-config-v1":
            raise Refused("General launch currently supports ordinary fold-fit configs only")
        if spec.kind == "simulation" and config.get("execution") is None:
            raise Refused("Simulation specification requires execution")
        if spec.kind != "simulation" and config.get("execution") is not None:
            raise Refused("Execution differs from the frozen experiment kind")
        if spec.kind == "predictive" and not config.get("evaluators"):
            raise Refused("Predictive experiment requires forecast evaluation")
        if spec.kind == "descriptive" and (
            config.get("evaluators") or config.get("evaluation_outcomes")
        ):
            raise Refused("Descriptive specification cannot silently become predictive evaluation")
        data = config.get("dataset", {})
        if data.get("role") not in ("train", "validation"):
            raise Refused("General launch supports TRAIN/VALIDATION only")
        ref = DatasetRef(data["dataset_id"], data["manifest_sha256"], ViewRole(data["role"]))
        manifest = DatasetRegistry.discover(self.settings.data_roots).resolve(ref).manifest
        if classify_access(manifest, policy) is not AccessClassification.NORMAL:
            raise Refused("Protected dataset refused before loading observations")
        actual = {
            "instrument": manifest.symbol,
            "timeframe": manifest.timeframe,
            "clock_domain": manifest.clock_domain.identifier,
            "time_unit": manifest.time_unit.value,
            "role": manifest.role.value,
        }
        for field, value in actual.items():
            if getattr(spec.data, field) != value:
                raise Refused(f"Dataset {field} differs from frozen specification")
        for field in ("dataset_id", "manifest_sha256"):
            expected = getattr(spec.data, field)
            if expected is not None and expected != data[field]:
                raise Refused(f"Frozen dataset {field} changed")
        if set(spec.data.required_columns) - {c.name for c in manifest.columns}:
            raise Refused("Dataset lacks source columns required by the specification")
        selections = {}
        for field, kind in {
            "features": "feature",
            "policies": "decision",
            "evaluation_outcomes": "outcome",
            "evaluators": "evaluator",
            "trade_evaluators": "evaluator",
        }.items():
            for index, selection in enumerate(config.get(field, [])):
                selections[f"/{field}/{index}"] = (kind, selection)
        for field, kind in {
            "model": "model",
            "representation": "representation",
            "training_outcome": "outcome",
        }.items():
            if config.get(field) is not None:
                selections[f"/{field}"] = (kind, config[field])
        requirements = {c.name: c for c in spec.components}
        if any(c.kind == "data" for c in spec.components):
            selections["/dataset"] = ("data", data)
            for component in spec.components:
                if component.kind == "data" and (
                    not component.inputs
                    or set(component.inputs) - {c.name for c in manifest.columns}
                ):
                    raise Refused("Data dependency needs explicit available source columns")
        if set(bindings) != set(requirements):
            raise Refused("Every frozen component needs an explicit config binding")
        covered = []
        for name, pointers in bindings.items():
            if not pointers:
                raise Refused(f"Empty binding for {name}")
            for pointer in pointers:
                if pointer not in selections or selections[pointer][0] != requirements[name].kind:
                    raise Refused(f"Wrong or missing component binding: {name} -> {pointer}")
                covered.append(pointer)
        if len(set(covered)) != len(covered) or set(covered) != set(selections):
            raise Refused("Bindings must cover each configured component exactly once")
        return policy_relative

    def _engine(
        self,
        command: str,
        config_path,
        policy_relative,
        *,
        timeout: int,
        expected_fingerprint: str | None = None,
        limits: dict[str, int] | None = None,
    ):
        root = self.settings.suite_root
        if limits is None:
            raise Refused("Structured execution limits are required")
        argv = [
            str(root / ".venv/bin/trading-research"),
            command,
            str(config_path),
            "--runs-root",
            str(self.settings.output_root),
            "--repository-root",
            str(root),
            "--dependency-lock",
            str(root / "uv.lock"),
            "--split-policy",
            str(root / policy_relative),
            "--audit",
            str(self.settings.output_root / "audit.jsonl"),
            "--json",
            "--max-runtime-seconds",
            str(limits["max_runtime_seconds"]),
            "--max-memory-bytes",
            str(limits["max_memory_bytes"]),
        ]
        if command == "preflight":
            argv.extend(["--max-rows", str(limits["max_rows"])])
        if expected_fingerprint is not None:
            argv.extend(["--expected-fingerprint", expected_fingerprint])
        for data_root in self.settings.data_roots:
            argv.extend(["--data-root", str(data_root)])
        environment = dict(os.environ)
        for name in (
            "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "VECLIB_MAXIMUM_THREADS",
        ):
            environment[name] = "1"
        completed = subprocess.run(
            argv, cwd=root, env=environment, text=True, capture_output=True, timeout=timeout
        )
        if completed.returncode:
            raise Refused((completed.stderr or completed.stdout)[-4000:].strip())
        return json.loads(completed.stdout)

    def check(
        self,
        experiment_id: str,
        revision: int,
        config: dict[str, Any],
        bindings: dict[str, list[str]],
        policy_name: str = "adr-0012-v1",
    ) -> dict[str, Any]:
        """Read permitted observations and calculate ephemeral features; no research run."""
        frozen = self.specs.get(experiment_id, revision)
        try:
            launches = self.settings.workspace / "launches"
            if launches.resolve() != launches.absolute():
                raise Refused("Launch records must not follow symlinks")
            attempts = 0
            if launches.exists():
                for path in launches.glob("*.json"):
                    receipt = read_json(launches, path.name)
                    if receipt.get("launch_state") != "launched":
                        raise Refused("An uncertain launch needs inspection before preparation")
                    if recorded_state(self.settings, receipt["run_id"])["state"] not in TERMINAL:
                        raise Refused("An adapter-launched run is still active")
                    attempts += (
                        receipt.get("experiment_id") == experiment_id
                        and receipt.get("revision") == revision
                    )
            if attempts >= frozen["specification"]["evaluation"]["max_runs"]:
                raise Refused("The frozen experiment's run budget is exhausted")
            limits = frozen["approval"].get("limits")
            if limits is None:
                raise Refused("Freeze an approved revision with structured execution limits first")
            if len(encoded(config)) > 500_000:
                raise Refused("Configuration exceeds the supported size")
            policy_relative = self._binding(frozen, config, bindings, policy_name)
            directory = self._mkdir("checks/" + uuid.uuid4().hex)
            config_path = directory / "config.json"
            atomic_json(config_path, config)
            resolved = load_resolved_config(config_path).document()
            if resolved != config:
                raise Refused("Supply the complete canonical resolved configuration")
            before = self._checkout(require_clean=False)
            engine = self._engine(
                "preflight",
                config_path,
                policy_relative,
                timeout=limits["max_runtime_seconds"] + 30,
                limits=limits,
            )
            after = self._checkout(require_clean=False)
            if after != before:
                raise Refused("Checkout changed during input checks")
            blockers = []
            if before["dirty"] or not engine.get("canonical_candidate"):
                blockers.append("Commit the verified code and synchronize the engine lockfile")
            try:
                verification = self._verification(before)
            except (OSError, ValueError):
                verification = None
                blockers.append("Run experiment_verify for this exact clean checkout")
            if frozen["approval"]["scope"] != "integrate_and_run":
                blockers.append("Frozen authorization covers integration only")
            result = {
                "ready_to_run": not blockers,
                "blockers": blockers,
                "spec_sha256": frozen["spec_sha256"],
                "config_sha256": digest(encoded(config)),
                "checkout": before,
                "engine": engine,
                "limits": limits,
                "policy_relative": policy_relative,
                "policy_sha256": digest(read_bytes(self.settings.suite_root, policy_relative)),
                "data_roots": [str(p) for p in self.settings.data_roots],
                "output_root": str(self.settings.output_root),
                "verification_sha256": None
                if verification is None
                else digest(encoded(verification)),
            }
            atomic_json(directory / "result.json", result)
            return result
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            TradingResearchError,
            subprocess.SubprocessError,
        ) as exc:
            return {
                "ready_to_run": False,
                "blockers": [str(exc)[:4000]],
                "spec_sha256": frozen["spec_sha256"],
            }

    def prepare(
        self,
        experiment_id: str,
        revision: int,
        config: dict[str, Any],
        bindings: dict[str, list[str]],
        policy_name: str = "adr-0012-v1",
    ) -> dict[str, Any]:
        check = self.check(experiment_id, revision, config, bindings, policy_name)
        if not check["ready_to_run"]:
            return check
        plan_id = uuid.uuid4().hex
        directory = self._mkdir("experiment-plans/" + plan_id)
        request = {
            "experiment_id": experiment_id,
            "revision": revision,
            "config": config,
            "bindings": bindings,
            "policy_name": policy_name,
        }
        plan = {
            "schema": "prepared-experiment-v1",
            "plan_id": plan_id,
            "request": request,
            "evidence": check,
        }
        atomic_json(directory / "config.json", config)
        atomic_json(directory / "plan.json", plan)
        return {"plan_id": plan_id, "ready_to_run": True, "evidence": check}

    def _plan(self, plan_id):
        if not PLAN_ID.fullmatch(plan_id):
            raise Refused("Invalid plan ID")
        directory = self.settings.workspace / "experiment-plans" / plan_id
        plan = read_json(directory, "plan.json")
        config = read_json(directory, "config.json")
        if (
            plan.get("schema") != "prepared-experiment-v1"
            or plan.get("plan_id") != plan_id
            or config != plan["request"]["config"]
            or digest(encoded(config)) != plan["evidence"]["config_sha256"]
        ):
            raise Refused("Prepared configuration changed")
        return directory, plan

    def start(self, plan_id: str) -> dict[str, Any]:
        directory, plan = self._plan(plan_id)
        launches = self._mkdir("launches")
        with self._lock("launch.lock"):
            if (launches / f"{plan_id}.json").exists():
                return read_json(launches, f"{plan_id}.json")
            previous = [read_json(launches, p.name) for p in launches.glob("*.json")]
            for receipt in previous:
                if receipt.get("launch_state") != "launched":
                    raise Refused(
                        "An uncertain launch requires inspection before any further launch"
                    )
                if recorded_state(self.settings, receipt["run_id"])["state"] not in TERMINAL:
                    raise Refused("An adapter-launched run is still active")
            frozen = self.specs.get(plan["request"]["experiment_id"], plan["request"]["revision"])
            attempts = sum(
                r.get("experiment_id") == plan["request"]["experiment_id"]
                and r.get("revision") == plan["request"]["revision"]
                for r in previous
            )
            if attempts >= frozen["specification"]["evaluation"]["max_runs"]:
                raise Refused("The frozen experiment's run budget is exhausted")
            checked = self.check(**plan["request"])
            if not checked.get("ready_to_run") or checked != plan["evidence"]:
                raise Refused(
                    "Prepared inputs changed or checks failed; prepare again: "
                    + "; ".join(checked.get("blockers", []))
                )
            if not (shutil.which("systemd-run") or shutil.which("tmux")):
                raise Refused("No supported detached launcher installed")
            receipt = {
                "workflow": "experiment",
                "plan_id": plan_id,
                "experiment_id": plan["request"]["experiment_id"],
                "revision": plan["request"]["revision"],
                "spec_sha256": frozen["spec_sha256"],
                "launch_state": "uncertain",
            }
            path = launches / f"{plan_id}.json"
            atomic_json(path, receipt)
            try:
                output = self._engine(
                    "run",
                    directory / "config.json",
                    checked["policy_relative"],
                    timeout=60,
                    expected_fingerprint=checked["engine"]["experiment_fingerprint"],
                    limits=checked["limits"],
                )
                if not RUN_ID.fullmatch(output["run_id"]):
                    raise Refused("Engine returned an invalid run ID")
                receipt.update(output, launch_state="launched")
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                receipt["message"] = (
                    "Launch outcome uncertain; inspect engine runs and this receipt. "
                    "Do not retry or delete it automatically."
                )
            atomic_json(path, receipt)
            return receipt

    def _run(self, plan_id):
        _, plan = self._plan(plan_id)
        receipt = read_json(self.settings.workspace / "launches", plan_id + ".json")
        if (
            receipt.get("workflow") != "experiment"
            or receipt.get("plan_id") != plan_id
            or receipt.get("launch_state") != "launched"
        ):
            raise Refused("This plan has no confirmed run")
        state = recorded_state(self.settings, receipt["run_id"])
        directory = self.settings.output_root / "runs" / receipt["run_id"]
        identity = read_json(directory, "identity.json")
        if identity.get("schema") != "research-experiment-identity-v1":
            raise Refused("Unsupported run identity schema")
        fingerprint = identity.get("experiment_fingerprint")
        inputs = identity.get("fingerprint_inputs", {})
        if (
            fingerprint != plan["evidence"]["engine"]["experiment_fingerprint"]
            or digest(encoded(inputs)) != fingerprint
        ):
            raise Refused("Run identity differs from prepared experiment")
        if (
            inputs.get("resolved_config") != plan["request"]["config"]
            or state.get("oos_touched") is not False
            or state.get("lockbox_touched") is not False
        ):
            raise Refused("Run configuration or access differs from preparation")
        return directory, state, identity

    def status(self, plan_id: str) -> dict[str, Any]:
        _, state, identity = self._run(plan_id)
        return {
            "run_id": state["run_id"],
            "state": state["state"],
            "updated_unix_ns": state["updated_unix_ns"],
            "experiment_fingerprint": identity["experiment_fingerprint"],
            "observation": "Recorded state; no reconciliation",
        }

    def results(self, plan_id: str) -> dict[str, Any]:
        directory, state, identity = self._run(plan_id)
        if state["state"] != "COMPLETE":
            raise Refused("Results require a COMPLETE run")
        manifest = read_json(directory, "manifest.json")
        if (
            manifest.get("schema") != "research-run-manifest-v3"
            or manifest.get("run_id") != state["run_id"]
            or manifest.get("state") != "COMPLETE"
            or manifest.get("experiment_fingerprint") != identity["experiment_fingerprint"]
        ):
            raise Refused("Completed run manifest identity mismatch")
        if manifest.get("platform") != identity["fingerprint_inputs"].get("platform") or any(
            manifest.get("access", {}).get(name) is not False
            for name in ("oos_touched", "lockbox_touched", "declared_lockbox_evaluation")
        ):
            raise Refused("Completed run platform or access evidence mismatch")
        for name in ("identity.json", "config.resolved.json"):
            if digest(read_bytes(directory, name)) != manifest["files"]["root"][name]:
                raise Refused("Run root artifact hash mismatch")
        payload = read_bytes(directory, "metrics/evaluation-summary.json")
        if digest(payload) != manifest["files"]["metrics"]["evaluation-summary.json"]:
            raise Refused("Evaluation summary hash mismatch")
        evaluation = json.loads(payload)
        if evaluation.get("schema") != "research-evaluation-inventory-v2":
            raise Refused("Unsupported evaluation inventory")
        for entry in evaluation["entries"]:
            for metric in entry["metrics"]:
                bits = metric["value_float64_bits"]
                value = None if bits is None else struct.unpack(">d", bytes.fromhex(bits))[0]
                metric["value"] = value if value is None or math.isfinite(value) else None
        simulation = None
        if "simulation-summary.json" in manifest["files"]["metrics"]:
            payload = read_bytes(directory, "metrics/simulation-summary.json")
            if digest(payload) != manifest["files"]["metrics"]["simulation-summary.json"]:
                raise Refused("Simulation summary hash mismatch")
            simulation = json.loads(payload)
        return {
            "run_id": state["run_id"],
            "state": "COMPLETE",
            "canonical": manifest["canonical"],
            "experiment_fingerprint": identity["experiment_fingerprint"],
            "evaluation": evaluation,
            "simulation": simulation,
            "source": "Hash-checked recorded artifacts; no new evaluation",
            "limitations": [
                "A completed run does not establish predictive edge.",
                "Simulation costs and execution remain unverified.",
                "Synthetic inputs demonstrate software behavior only.",
            ],
            "explorer": (
                ResearchService(self.settings).explorer(
                    state["run_id"], identity["experiment_fingerprint"]
                )
                if simulation is not None
                else {"available": False, "reason": "This experiment has no trade simulation"}
            ),
        }
