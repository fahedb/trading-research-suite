"""Strict, canonical Slice-3I resolved experiment configuration."""

from __future__ import annotations

import json as _json
import math as _math
import re as _re
import typing as _typing
from dataclasses import dataclass as _dataclass
from pathlib import Path as _Path
from types import MappingProxyType as _MappingProxyType

from trading_research.artifacts.container import _decode_params
from trading_research.artifacts.identity import _canonical_identity_json
from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.features import canonical_params_json as _canonical_params_json
from trading_research.core.identity import DatasetRef as _DatasetRef
from trading_research.core.identity import ViewRole as _ViewRole
from trading_research.core.simulation import CommissionBasis as _CommissionBasis
from trading_research.core.simulation import CostSemanticsStatus as _CostSemanticsStatus
from trading_research.core.simulation import ExecutionCostBinding as _ExecutionCostBinding
from trading_research.core.simulation import ExecutionPriceModel as _ExecutionPriceModel
from trading_research.core.simulation import ExecutionSpec as _ExecutionSpec
from trading_research.core.simulation import _execution_spec_params_json
from trading_research.data.gating import AccessReason as _AccessReason
from trading_research.data.gating import SplitPolicyIdentity as _SplitPolicyIdentity

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

__all__ = ["ResolvedExperimentConfig", "load_resolved_config"]

_SCHEMA_V1 = "research-experiment-config-v1"
_SCHEMA_V2 = "research-experiment-config-v2"
_INTEGRITY_MODE = "full"
_TIER_ONE_ROLES = frozenset(
    {
        _ViewRole.TRAIN,
        _ViewRole.VALIDATION,
        _ViewRole.OOS,
        _ViewRole.LOCKBOX,
    }
)


@_dataclass(frozen=True, slots=True)
class _ComponentSelection:
    _name: str
    _version: int
    _params: _Mapping[str, _ParamValue]

    def __post_init__(self) -> None:
        if type(self._name) is not str or not self._name or self._name != self._name.strip():
            raise _ContractViolationError("component name must be a non-empty trimmed string")
        if type(self._version) is not int or self._version <= 0:
            raise _ContractViolationError("component version must be a positive exact int")
        canonical = _canonical_params_json(self._params)
        decoded = _decode_params(_json.loads(canonical))
        object.__setattr__(self, "_params", _MappingProxyType(dict(decoded)))

    def _document(self) -> dict[str, object]:
        return {
            "name": self._name,
            "version": self._version,
            "params": _json.loads(_canonical_params_json(self._params)),
        }


@_dataclass(frozen=True, slots=True)
class _Bounds:
    _context_start: int
    _eval_start: int
    _eval_end: int

    def __post_init__(self) -> None:
        for value, label in (
            (self._context_start, "context_start"),
            (self._eval_start, "eval_start"),
            (self._eval_end, "eval_end"),
        ):
            if type(value) is not int or not -(2**63) <= value < 2**63:
                raise _ContractViolationError(f"{label} must be an exact int64")
        if not self._context_start <= self._eval_start <= self._eval_end:
            raise _ContractViolationError("resolved bounds must be ordered")


@_dataclass(frozen=True, slots=True)
class _PurgeOverrideConfig:
    _purge_rows: int
    _reason: str

    def __post_init__(self) -> None:
        if type(self._purge_rows) is not int or self._purge_rows < 0:
            raise _ContractViolationError("purge override rows must be a non-negative exact int")
        if (
            type(self._reason) is not str
            or not self._reason.strip()
            or self._reason != self._reason.strip()
        ):
            raise _ContractViolationError("purge override reason must be non-empty trimmed text")


@_dataclass(frozen=True, slots=True)
class _FoldConfig:
    _fit_rows: int
    _eval_rows: int
    _step_rows: int
    _embargo_rows: int
    _purge_override: _PurgeOverrideConfig | None

    def __post_init__(self) -> None:
        for value, label in (
            (self._fit_rows, "fit_rows"),
            (self._eval_rows, "eval_rows"),
            (self._step_rows, "step_rows"),
        ):
            if type(value) is not int or value <= 0:
                raise _ContractViolationError(f"{label} must be a positive exact int")
        if type(self._embargo_rows) is not int or self._embargo_rows < 0:
            raise _ContractViolationError("embargo_rows must be a non-negative exact int")
        if (
            self._purge_override is not None
            and type(self._purge_override) is not _PurgeOverrideConfig
        ):
            raise _ContractViolationError(
                "purge_override must be an exact resolved override or None"
            )


@_dataclass(frozen=True, slots=True)
class _FrozenStateConfig:
    _bundle_id: str
    _bundle_manifest_sha256: str
    _fitted_state_identity_key: str
    _fitted_state_artifact_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self._bundle_id) is not str
            or _re.fullmatch(
                r"(?:zone-tp-selector-v[12]|uls-entry-(?:selector|oof)-v1|"
                r"uls-entry-tp-selector-v1|uls-joint-action-selector-v1)-[0-9a-f]{20}",
                self._bundle_id,
            )
            is None
        ):
            raise _ContractViolationError("frozen bundle_id is malformed")
        for value, label in (
            (self._bundle_manifest_sha256, "bundle_manifest_sha256"),
            (self._fitted_state_identity_key, "fitted_state_identity_key"),
            (self._fitted_state_artifact_sha256, "fitted_state_artifact_sha256"),
        ):
            if type(value) is not str or _re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise _ContractViolationError(f"frozen {label} must be SHA-256")

    def _document(self) -> dict[str, object]:
        return {
            "mode": "frozen_state_v1",
            "bundle_id": self._bundle_id,
            "bundle_manifest_sha256": self._bundle_manifest_sha256,
            "fitted_state_identity_key": self._fitted_state_identity_key,
            "fitted_state_artifact_sha256": self._fitted_state_artifact_sha256,
        }


@_dataclass(frozen=True, slots=True, init=False)
class ResolvedExperimentConfig:
    """One complete semantic experiment definition with no machine paths."""

    _dataset_ref: _DatasetRef
    _split_policy_identity: _SplitPolicyIdentity
    _bounds: _Bounds
    _features: tuple[_ComponentSelection, ...]
    _representation: _ComponentSelection | None
    _model: _ComponentSelection
    _training_outcome: _ComponentSelection | None
    _evaluation_outcomes: tuple[_ComponentSelection, ...]
    _policies: tuple[_ComponentSelection, ...]
    _evaluators: tuple[_ComponentSelection, ...]
    _execution: _ExecutionSpec | None
    _trade_evaluators: tuple[_ComponentSelection, ...]
    _folds: _FoldConfig | None
    _frozen_state: _FrozenStateConfig | None
    _seed: int | None
    _access_reason: _AccessReason | None
    _declared_lockbox_evaluation: bool
    _canonical_json: str

    def __init__(self) -> None:
        """Require strict parsing so no caller can construct a partial config."""
        raise TypeError("ResolvedExperimentConfig instances are issued by load_resolved_config")

    @classmethod
    def _from_document(cls, value: object) -> ResolvedExperimentConfig:
        if type(value) is not dict:
            raise _ContractViolationError("resolved config must be an exact object")
        schema_value = value.get("schema")
        if schema_value not in {_SCHEMA_V1, _SCHEMA_V2}:
            raise _ContractViolationError("resolved config schema is unsupported")
        if schema_value == _SCHEMA_V1:
            document = _exact_dict(
                value,
                fields={
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
                },
                label="resolved config",
            )
        else:
            document = _exact_dict(
                value,
                fields={
                    "access_reason",
                    "bounds",
                    "dataset",
                    "declared_lockbox_evaluation",
                    "evaluation_outcomes",
                    "evaluators",
                    "execution",
                    "features",
                    "fit_mode",
                    "integrity_mode",
                    "model",
                    "policies",
                    "representation",
                    "schema",
                    "seed",
                    "split_policy",
                    "training_outcome",
                    "trade_evaluators",
                },
                label="resolved config",
            )
        if document["integrity_mode"] != _INTEGRITY_MODE:
            raise _ContractViolationError("integrity_mode must be exactly 'full'")

        dataset = _exact_dict(
            document["dataset"],
            fields={"dataset_id", "manifest_sha256", "role"},
            label="resolved dataset",
        )
        role_value = dataset["role"]
        if type(role_value) is not str:
            raise _ContractViolationError("dataset role must be exact text")
        try:
            role = _ViewRole(role_value)
        except (TypeError, ValueError) as exc:
            raise _ContractViolationError("dataset role is unsupported") from exc
        if role not in _TIER_ONE_ROLES:
            raise _ContractViolationError(
                "Phase-3 experiments require one TRAIN, VALIDATION, OOS, or LOCKBOX view"
            )
        dataset_ref = _DatasetRef(
            _exact_text(dataset["dataset_id"], label="dataset_id"),
            _exact_text(dataset["manifest_sha256"], label="manifest_sha256"),
            role,
        )

        split = _exact_dict(
            document["split_policy"],
            fields={"adr", "policy_sha256", "policy_version"},
            label="resolved split policy",
        )
        split_identity = _SplitPolicyIdentity(
            _exact_text(split["adr"], label="split policy adr"),
            _exact_text(split["policy_version"], label="split policy version"),
            _exact_text(split["policy_sha256"], label="split policy digest"),
        )

        bounds_value = _exact_dict(
            document["bounds"],
            fields={"context_start", "eval_end", "eval_start"},
            label="resolved bounds",
        )
        bounds = _Bounds(
            _exact_int(bounds_value["context_start"], label="context_start"),
            _exact_int(bounds_value["eval_start"], label="eval_start"),
            _exact_int(bounds_value["eval_end"], label="eval_end"),
        )

        features = _component_sequence(
            document["features"],
            label="features",
            allow_empty=False,
        )
        representation = _optional_component(
            document["representation"],
            label="representation",
        )
        model = _component(document["model"], label="model")
        training_outcome = _optional_component(
            document["training_outcome"],
            label="training_outcome",
        )
        evaluation_outcomes = _component_sequence(
            document["evaluation_outcomes"],
            label="evaluation_outcomes",
            allow_empty=True,
        )
        policies = _component_sequence(
            document["policies"],
            label="policies",
            allow_empty=False,
        )
        evaluators = _component_sequence(
            document["evaluators"],
            label="evaluators",
            allow_empty=True,
        )
        if bool(evaluation_outcomes) is not bool(evaluators):
            raise _ContractViolationError(
                "evaluation_outcomes and evaluators must either both be non-empty or both empty"
            )
        execution = _optional_execution_spec(document["execution"])
        trade_evaluators = _component_sequence(
            document["trade_evaluators"],
            label="trade_evaluators",
            allow_empty=True,
        )
        if trade_evaluators and execution is None:
            raise _ContractViolationError("trade_evaluators require an explicit execution block")

        folds: _FoldConfig | None
        frozen_state: _FrozenStateConfig | None
        override: _PurgeOverrideConfig | None
        if schema_value == _SCHEMA_V1:
            folds_value = _exact_dict(
                document["folds"],
                fields={
                    "embargo_rows",
                    "eval_rows",
                    "fit_rows",
                    "purge_override",
                    "step_rows",
                },
                label="fold config",
            )
            override_value = folds_value["purge_override"]
            if override_value is None:
                override = None
            else:
                override_document = _exact_dict(
                    override_value,
                    fields={"purge_rows", "reason"},
                    label="purge override",
                )
                override = _PurgeOverrideConfig(
                    _exact_int(override_document["purge_rows"], label="purge_rows"),
                    _exact_text(override_document["reason"], label="purge reason"),
                )
            folds = _FoldConfig(
                _exact_int(folds_value["fit_rows"], label="fit_rows"),
                _exact_int(folds_value["eval_rows"], label="eval_rows"),
                _exact_int(folds_value["step_rows"], label="step_rows"),
                _exact_int(folds_value["embargo_rows"], label="embargo_rows"),
                override,
            )
            frozen_state = None
        else:
            fit_mode = _exact_dict(
                document["fit_mode"],
                fields={
                    "bundle_id",
                    "bundle_manifest_sha256",
                    "fitted_state_artifact_sha256",
                    "fitted_state_identity_key",
                    "mode",
                },
                label="fit_mode",
            )
            if fit_mode["mode"] != "frozen_state_v1":
                raise _ContractViolationError("V2 fit_mode must be frozen_state_v1")
            frozen_state = _FrozenStateConfig(
                _exact_text(fit_mode["bundle_id"], label="bundle_id"),
                _exact_text(
                    fit_mode["bundle_manifest_sha256"],
                    label="bundle_manifest_sha256",
                ),
                _exact_text(
                    fit_mode["fitted_state_identity_key"],
                    label="fitted_state_identity_key",
                ),
                _exact_text(
                    fit_mode["fitted_state_artifact_sha256"],
                    label="fitted_state_artifact_sha256",
                ),
            )
            folds = None
            override = None
            if training_outcome is not None:
                raise _ContractViolationError(
                    "frozen-state configs cannot declare training_outcome"
                )
            if representation is not None:
                raise _ContractViolationError("frozen-state v1 does not support representations")

        seed_value = document["seed"]
        if seed_value is not None and type(seed_value) is not int:
            raise _ContractViolationError("seed must be an exact int or None")
        access_value = document["access_reason"]
        access_reason: _AccessReason | None
        if access_value is None:
            access_reason = None
        else:
            access = _exact_dict(
                access_value,
                fields={"code", "detail"},
                label="access reason",
            )
            access_reason = _AccessReason(
                _exact_text(access["code"], label="access reason code"),
                _exact_text(access["detail"], label="access reason detail"),
            )
        declared = document["declared_lockbox_evaluation"]
        if type(declared) is not bool:
            raise _ContractViolationError("declared_lockbox_evaluation must be an exact bool")
        if role in {_ViewRole.TRAIN, _ViewRole.VALIDATION}:
            if access_reason is not None or declared:
                raise _ContractViolationError(
                    "TRAIN and VALIDATION configs must not declare gated-access ceremony"
                )
        elif role is _ViewRole.OOS:
            if access_reason is None or declared:
                raise _ContractViolationError(
                    "OOS configs require an access_reason and cannot declare lockbox evaluation"
                )
        elif access_reason is None or not declared:
            raise _ContractViolationError(
                "LOCKBOX configs require an access_reason and declared_lockbox_evaluation=true"
            )

        canonical_document: dict[str, object] = {
            "schema": schema_value,
            "integrity_mode": _INTEGRITY_MODE,
            "dataset": {
                "dataset_id": dataset_ref.dataset_id,
                "manifest_sha256": dataset_ref.manifest_sha256,
                "role": dataset_ref.role.value,
            },
            "split_policy": {
                "adr": split_identity.adr,
                "policy_version": split_identity.policy_version,
                "policy_sha256": split_identity.policy_sha256,
            },
            "bounds": {
                "context_start": bounds._context_start,
                "eval_start": bounds._eval_start,
                "eval_end": bounds._eval_end,
            },
            "features": [item._document() for item in features],
            "representation": (None if representation is None else representation._document()),
            "model": model._document(),
            "training_outcome": (
                None if training_outcome is None else training_outcome._document()
            ),
            "evaluation_outcomes": [item._document() for item in evaluation_outcomes],
            "policies": [item._document() for item in policies],
            "evaluators": [item._document() for item in evaluators],
            "execution": (
                None if execution is None else _json.loads(_execution_spec_params_json(execution))
            ),
            "trade_evaluators": [item._document() for item in trade_evaluators],
            "seed": seed_value,
            "access_reason": (
                None
                if access_reason is None
                else {"code": access_reason.code, "detail": access_reason.detail}
            ),
            "declared_lockbox_evaluation": declared,
        }
        if folds is not None:
            canonical_document["folds"] = {
                "fit_rows": folds._fit_rows,
                "eval_rows": folds._eval_rows,
                "step_rows": folds._step_rows,
                "embargo_rows": folds._embargo_rows,
                "purge_override": (
                    None
                    if override is None
                    else {
                        "purge_rows": override._purge_rows,
                        "reason": override._reason,
                    }
                ),
            }
        elif frozen_state is not None:
            canonical_document["fit_mode"] = frozen_state._document()
        else:  # pragma: no cover - parser constructs exactly one mode
            raise AssertionError("resolved experiment has no fit mode")
        canonical = _canonical_identity_json(canonical_document)
        config = object.__new__(cls)
        for name, item in (
            ("_dataset_ref", dataset_ref),
            ("_split_policy_identity", split_identity),
            ("_bounds", bounds),
            ("_features", features),
            ("_representation", representation),
            ("_model", model),
            ("_training_outcome", training_outcome),
            ("_evaluation_outcomes", evaluation_outcomes),
            ("_policies", policies),
            ("_evaluators", evaluators),
            ("_execution", execution),
            ("_trade_evaluators", trade_evaluators),
            ("_folds", folds),
            ("_frozen_state", frozen_state),
            ("_seed", seed_value),
            ("_access_reason", access_reason),
            ("_declared_lockbox_evaluation", declared),
            ("_canonical_json", canonical),
        ):
            object.__setattr__(config, name, item)
        return config

    def canonical_json(self) -> str:
        """Return the complete deterministic fingerprint input document."""
        return self._canonical_json

    def document(self) -> dict[str, object]:
        """Return a detached JSON-native copy of the resolved configuration."""
        value = _json.loads(self._canonical_json)
        if type(value) is not dict:  # pragma: no cover - construction proves this
            raise AssertionError("resolved config canonical root is not an object")
        return _typing.cast("dict[str, object]", value)


def load_resolved_config(path: _Path) -> ResolvedExperimentConfig:
    """Parse one strict JSON config and normalize it to its closed schema."""
    if not isinstance(path, _Path):
        raise _ContractViolationError("config path must be a pathlib.Path")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise _ContractViolationError(f"cannot read resolved config {path}: {exc}") from exc
    return _config_from_bytes(payload, label=str(path))


def _config_from_document(value: object) -> ResolvedExperimentConfig:
    return ResolvedExperimentConfig._from_document(value)


def _config_from_bytes(payload: bytes, *, label: str) -> ResolvedExperimentConfig:
    if type(payload) is not bytes:
        raise _ContractViolationError("resolved config payload must be exact bytes")

    def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError(f"duplicate field {name!r}")
            result[name] = value
        return result

    try:
        value = _json.loads(
            payload,
            object_pairs_hook=_pairs,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"non-standard JSON constant {item}")
            ),
        )
    except (UnicodeDecodeError, ValueError, _json.JSONDecodeError) as exc:
        raise _ContractViolationError(f"resolved config {label} is not strict JSON") from exc
    _reject_nonfinite(value, label="resolved config")
    return _config_from_document(value)


def _component(value: object, *, label: str) -> _ComponentSelection:
    document = _exact_dict(
        value,
        fields={"name", "params", "version"},
        label=label,
    )
    params_value = document["params"]
    if type(params_value) is not dict:
        raise _ContractViolationError(f"{label} params must be an exact object")
    try:
        params = _decode_params(params_value)
    except Exception as exc:
        raise _ContractViolationError(f"{label} params are not canonical values") from exc
    return _ComponentSelection(
        _exact_text(document["name"], label=f"{label} name"),
        _exact_int(document["version"], label=f"{label} version"),
        params,
    )


def _optional_execution_spec(value: object) -> _ExecutionSpec | None:
    if value is None:
        return None
    document = _exact_dict(
        value,
        fields={
            "cost_binding",
            "execution_id",
            "lot_size",
            "stop_loss_pips",
            "take_profit_pips",
            "version",
        },
        label="execution",
    )
    binding_document = _exact_dict(
        document["cost_binding"],
        fields={
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
        label="execution cost_binding",
    )
    try:
        outer = _decode_params(
            {
                name: document[name]
                for name in (
                    "execution_id",
                    "lot_size",
                    "stop_loss_pips",
                    "take_profit_pips",
                    "version",
                )
            }
        )
        binding_values = _decode_params(binding_document)
    except Exception as exc:
        raise _ContractViolationError(
            "execution values do not use canonical parameter encoding"
        ) from exc
    try:
        cost_semantics = _CostSemanticsStatus(_typing.cast("str", binding_values["cost_semantics"]))
        price_model = _ExecutionPriceModel(
            _typing.cast("str", binding_values["execution_price_model"])
        )
        commission_basis = _CommissionBasis(_typing.cast("str", binding_values["commission_basis"]))
    except (TypeError, ValueError) as exc:
        raise _ContractViolationError("execution enum value is unsupported") from exc
    if cost_semantics is not _CostSemanticsStatus.UNVERIFIED:
        raise _ContractViolationError(
            "config resolution permits only unverified cost semantics while the audit is OPEN"
        )
    if price_model is not _ExecutionPriceModel.RECORDED_PATH_UNVERIFIED:
        raise _ContractViolationError(
            "config resolution permits only recorded_path_unverified in V1"
        )
    try:
        binding = _ExecutionCostBinding(
            binding_id=_typing.cast("str", binding_values["binding_id"]),
            binding_digest=_typing.cast("str | None", binding_values["binding_digest"]),
            cost_semantics=cost_semantics,
            execution_price_model=price_model,
            contract_size=_typing.cast("float", binding_values["contract_size"]),
            price_units_per_pip=_typing.cast("float", binding_values["price_units_per_pip"]),
            account_currency_per_quote_currency=_typing.cast(
                "float", binding_values["account_currency_per_quote_currency"]
            ),
            spread_pips=_typing.cast("float", binding_values["spread_pips"]),
            commission_per_lot=_typing.cast("float", binding_values["commission_per_lot"]),
            commission_basis=commission_basis,
        )
        return _ExecutionSpec(
            execution_id=_typing.cast("str", outer["execution_id"]),
            version=_typing.cast("int", outer["version"]),
            lot_size=_typing.cast("float", outer["lot_size"]),
            take_profit_pips=_typing.cast("float | None", outer["take_profit_pips"]),
            stop_loss_pips=_typing.cast("float | None", outer["stop_loss_pips"]),
            cost_binding=binding,
        )
    except (KeyError, TypeError) as exc:
        raise _ContractViolationError("execution fields are malformed") from exc


def _optional_component(value: object, *, label: str) -> _ComponentSelection | None:
    return None if value is None else _component(value, label=label)


def _component_sequence(
    value: object,
    *,
    label: str,
    allow_empty: bool,
) -> tuple[_ComponentSelection, ...]:
    if type(value) is not list:
        raise _ContractViolationError(f"{label} must be an exact array")
    result = tuple(_component(item, label=f"{label}[{index}]") for index, item in enumerate(value))
    if not result and not allow_empty:
        raise _ContractViolationError(f"{label} must not be empty")
    canonical = tuple(_canonical_identity_json(item._document()) for item in result)
    if len(set(canonical)) != len(canonical):
        raise _ContractViolationError(f"{label} contains an exact duplicate")
    return result


def _exact_dict(
    value: object,
    *,
    fields: set[str],
    label: str,
) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise _ContractViolationError(f"{label} fields differ from the V1 schema")
    return _typing.cast("dict[str, object]", value)


def _exact_text(value: object, *, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise _ContractViolationError(f"{label} must be non-empty trimmed text")
    return value


def _exact_int(value: object, *, label: str) -> int:
    if type(value) is not int:
        raise _ContractViolationError(f"{label} must be an exact int")
    return value


def _reject_nonfinite(value: object, *, label: str) -> None:
    if type(value) is float and not _math.isfinite(value):
        raise _ContractViolationError(f"{label} contains a non-finite number")
    if type(value) is dict:
        for item in _typing.cast("dict[object, object]", value).values():
            _reject_nonfinite(item, label=label)
    elif type(value) is list:
        for item in _typing.cast("list[object]", value):
            _reject_nonfinite(item, label=label)
