"""Immutable Phase-4 simulation contracts and evidence-bound execution values.

This module defines vocabulary only. It performs no simulation, data loading,
configuration resolution, persistence, registry lookup, or artifact I/O.
"""

from __future__ import annotations

import hashlib as _hashlib
import json as _json
import math as _math
import re as _re
import typing as _typing
from collections.abc import Mapping as _Mapping
from dataclasses import dataclass as _dataclass
from enum import StrEnum as _StrEnum
from types import MappingProxyType as _MappingProxyType

import numpy as _np

from trading_research.core.errors import ContractViolationError as _ContractViolationError
from trading_research.core.features import ParamValue as _ParamValue
from trading_research.core.features import canonical_params_json as _canonical_params_json
from trading_research.core.identity import DatasetIdentity as _DatasetIdentity
from trading_research.core.research import StageOrigin as _StageOrigin
from trading_research.core.time import ClockDomain as _ClockDomain
from trading_research.core.time import TimeUnit as _TimeUnit

if _typing.TYPE_CHECKING:
    import numpy.typing as _npt

__all__ = [
    "VERIFIED_COST_BINDING",
    "CommissionBasis",
    "CostSemanticsStatus",
    "EquityCurve",
    "ExecutionCostBinding",
    "ExecutionPriceModel",
    "ExecutionSpec",
    "SimulationRequest",
    "TradeExitReason",
    "TradeLog",
]

_SHA256_LOWER_HEX = _re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_EXECUTION_IDS = (
    "bracket_single_position_v1",
    "bracket_non_interruptible_v1",
)


class CommissionBasis(_StrEnum):
    """Whether configured commission is charged per side or per round turn."""

    PER_SIDE = "per_side"
    PER_ROUND_TURN = "per_round_turn"


class CostSemanticsStatus(_StrEnum):
    """Evidence status permanently attached to one execution-cost binding."""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"


class ExecutionPriceModel(_StrEnum):
    """Closed V1 declaration of how the executable price path is interpreted."""

    RECORDED_PATH_UNVERIFIED = "recorded_path_unverified"
    SYNTHETIC_DECLARED = "synthetic_declared"


class TradeExitReason(_StrEnum):
    """Closed reasons for a completed trade in chronological trade artifacts."""

    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TARGET_CHANGE = "target_change"
    BOUNDARY_FORCED_CLOSE = "boundary_forced_close"


_TRADE_EXIT_REASON_CODES = _MappingProxyType(
    {
        TradeExitReason.TAKE_PROFIT: 1,
        TradeExitReason.STOP_LOSS: 2,
        TradeExitReason.TARGET_CHANGE: 3,
        TradeExitReason.BOUNDARY_FORCED_CLOSE: 4,
    }
)


def _trade_exit_reason_code(reason: TradeExitReason) -> int:
    """Return the pinned int8 artifact code for one exact exit reason."""
    if type(reason) is not TradeExitReason:
        raise _ContractViolationError("trade exit reason must be an exact TradeExitReason")
    return _TRADE_EXIT_REASON_CODES[reason]


def _validated_float(
    value: object,
    *,
    field: str,
    positive: bool,
) -> float:
    if type(value) is not float or not _math.isfinite(value):
        raise _ContractViolationError(f"{field} must be a finite Python float")
    if positive and value <= 0.0:
        raise _ContractViolationError(f"{field} must be greater than zero")
    if not positive and value < 0.0:
        raise _ContractViolationError(f"{field} must be non-negative")
    return value


def _validated_optional_positive_float(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    return _validated_float(value, field=field, positive=True)


def _validated_sources(
    source_identities: tuple[_DatasetIdentity, ...],
) -> tuple[_DatasetIdentity, ...]:
    if type(source_identities) is not tuple or not source_identities:
        raise _ContractViolationError("source_identities must be a non-empty exact tuple")
    if any(type(identity) is not _DatasetIdentity for identity in source_identities):
        raise _ContractViolationError(
            "source_identities must contain exact DatasetIdentity values"
        )
    if len(set(source_identities)) != len(source_identities):
        raise _ContractViolationError("source_identities must be unique")
    return source_identities


def _validate_axis_tags(
    *,
    unit: _TimeUnit,
    clock_domain: _ClockDomain,
    source_identities: tuple[_DatasetIdentity, ...],
) -> None:
    if type(unit) is not _TimeUnit:
        raise _ContractViolationError("unit must be an exact TimeUnit")
    if type(clock_domain) is not _ClockDomain:
        raise _ContractViolationError("clock_domain must be an exact ClockDomain")
    _validated_sources(source_identities)


def _binding_params(
    binding: ExecutionCostBinding,
    *,
    include_digest: bool,
) -> dict[str, _ParamValue]:
    params: dict[str, _ParamValue] = {
        "account_currency_per_quote_currency": binding.account_currency_per_quote_currency,
        "binding_id": binding.binding_id,
        "commission_basis": binding.commission_basis.value,
        "commission_per_lot": binding.commission_per_lot,
        "contract_size": binding.contract_size,
        "cost_semantics": binding.cost_semantics.value,
        "execution_price_model": binding.execution_price_model.value,
        "price_units_per_pip": binding.price_units_per_pip,
        "spread_pips": binding.spread_pips,
    }
    if include_digest:
        params["binding_digest"] = binding.binding_digest
    return params


def _execution_cost_binding_params_json(
    binding: ExecutionCostBinding,
    *,
    include_digest: bool = True,
) -> str:
    """Return the canonical flat parameter document for one cost binding."""
    if type(binding) is not ExecutionCostBinding:
        raise _ContractViolationError(
            "canonical cost parameters require an exact ExecutionCostBinding"
        )
    return _canonical_params_json(_binding_params(binding, include_digest=include_digest))


def _execution_cost_binding_digest(binding: ExecutionCostBinding) -> str:
    """Hash the canonical binding document while excluding its declared digest."""
    canonical = _execution_cost_binding_params_json(binding, include_digest=False)
    return _hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _execution_spec_params_json(spec: ExecutionSpec) -> str:
    """Return canonical execution parameters with the binding as a nested block."""
    if type(spec) is not ExecutionSpec:
        raise _ContractViolationError(
            "canonical execution parameters require an exact ExecutionSpec"
        )
    outer = _typing.cast(
        "dict[str, object]",
        _json.loads(
            _canonical_params_json(
                {
                    "execution_id": spec.execution_id,
                    "lot_size": spec.lot_size,
                    "stop_loss_pips": spec.stop_loss_pips,
                    "take_profit_pips": spec.take_profit_pips,
                    "version": spec.version,
                }
            )
        ),
    )
    outer["cost_binding"] = _typing.cast(
        "dict[str, object]",
        _json.loads(_execution_cost_binding_params_json(spec.cost_binding)),
    )
    return _json.dumps(
        outer,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )


@_dataclass(frozen=True, slots=True)
class ExecutionCostBinding:
    """Explicit contingent cost, unit, evidence, and price-path semantics."""

    binding_id: str
    binding_digest: str | None
    cost_semantics: CostSemanticsStatus
    execution_price_model: ExecutionPriceModel
    contract_size: float
    price_units_per_pip: float
    account_currency_per_quote_currency: float
    spread_pips: float
    commission_per_lot: float
    commission_basis: CommissionBasis

    def __post_init__(self) -> None:
        """Validate every explicit field without inventing an evidence default."""
        if type(self.binding_id) is not str or not self.binding_id:
            raise _ContractViolationError("binding_id must be a non-empty string")
        if type(self.cost_semantics) is not CostSemanticsStatus:
            raise _ContractViolationError(
                "cost_semantics must be an exact CostSemanticsStatus"
            )
        if type(self.execution_price_model) is not ExecutionPriceModel:
            raise _ContractViolationError(
                "execution_price_model must be an exact ExecutionPriceModel"
            )
        if type(self.commission_basis) is not CommissionBasis:
            raise _ContractViolationError("commission_basis must be an exact CommissionBasis")

        _validated_float(self.contract_size, field="contract_size", positive=True)
        _validated_float(
            self.price_units_per_pip,
            field="price_units_per_pip",
            positive=True,
        )
        _validated_float(
            self.account_currency_per_quote_currency,
            field="account_currency_per_quote_currency",
            positive=True,
        )
        _validated_float(self.spread_pips, field="spread_pips", positive=False)
        _validated_float(
            self.commission_per_lot,
            field="commission_per_lot",
            positive=False,
        )

        digest = self.binding_digest
        if self.cost_semantics is CostSemanticsStatus.UNVERIFIED:
            if digest is not None:
                raise _ContractViolationError(
                    "binding_digest must be None when cost_semantics is UNVERIFIED"
                )
        elif type(digest) is not str or _SHA256_LOWER_HEX.fullmatch(digest) is None:
            raise _ContractViolationError(
                "binding_digest must be 64 lowercase hex characters when cost semantics "
                "are VERIFIED"
            )


# Evidence authority is repository state, never configuration. The audit-closing
# commit is the only change allowed to replace this value.
VERIFIED_COST_BINDING: _typing.Final[ExecutionCostBinding | None] = None


def _validate_evidence_binding(binding: ExecutionCostBinding) -> None:
    if binding.cost_semantics is CostSemanticsStatus.UNVERIFIED:
        return
    if binding.execution_price_model is ExecutionPriceModel.RECORDED_PATH_UNVERIFIED:
        raise _ContractViolationError(
            "RECORDED_PATH_UNVERIFIED cannot back VERIFIED cost semantics"
        )
    expected = VERIFIED_COST_BINDING
    if expected is None:
        raise _ContractViolationError(
            "VERIFIED cost semantics are unavailable while VERIFIED_COST_BINDING is None"
        )
    if type(expected) is not ExecutionCostBinding:
        raise _ContractViolationError(
            "VERIFIED_COST_BINDING must be an exact ExecutionCostBinding or None"
        )
    recomputed = _execution_cost_binding_digest(binding)
    if binding.binding_digest != recomputed:
        raise _ContractViolationError(
            "binding_digest does not match the platform-recomputed binding digest"
        )
    if binding != expected:
        raise _ContractViolationError(
            "VERIFIED cost binding must equal VERIFIED_COST_BINDING field-for-field"
        )


@_dataclass(frozen=True, slots=True)
class ExecutionSpec:
    """One explicit fixed-lot bracket execution configuration."""

    execution_id: str
    version: int
    lot_size: float
    take_profit_pips: float | None
    stop_loss_pips: float | None
    cost_binding: ExecutionCostBinding

    def __post_init__(self) -> None:
        """Validate the closed simulator identity and fail closed on evidence."""
        if type(self.execution_id) is not str or self.execution_id not in _SUPPORTED_EXECUTION_IDS:
            raise _ContractViolationError(
                f"execution_id must be one of {_SUPPORTED_EXECUTION_IDS!r}"
            )
        if type(self.version) is not int or self.version <= 0:
            raise _ContractViolationError("execution version must be a positive Python int")
        _validated_float(self.lot_size, field="lot_size", positive=True)
        _validated_optional_positive_float(
            self.take_profit_pips,
            field="take_profit_pips",
        )
        _validated_optional_positive_float(
            self.stop_loss_pips,
            field="stop_loss_pips",
        )
        if type(self.cost_binding) is not ExecutionCostBinding:
            raise _ContractViolationError(
                "cost_binding must be an exact ExecutionCostBinding"
            )
        _validate_evidence_binding(self.cost_binding)


@_dataclass(frozen=True, slots=True)
class _ImmutableArrayStorage:
    _buffer: bytes
    _dtype: _np.dtype[_np.generic]
    _length: int

    @classmethod
    def _from_array(cls, array: _npt.NDArray[_typing.Any]) -> _ImmutableArrayStorage:
        contiguous = _np.ascontiguousarray(array)
        return cls(
            _buffer=contiguous.tobytes(order="C"),
            _dtype=contiguous.dtype,
            _length=int(contiguous.size),
        )

    def _descriptor(self) -> _npt.NDArray[_typing.Any]:
        descriptor = _np.frombuffer(self._buffer, dtype=self._dtype, count=self._length)
        descriptor.setflags(write=False)
        return descriptor


def _validated_array(
    values: object,
    *,
    field: str,
    dtype: _np.dtype[_np.generic],
    length: int | None = None,
) -> _npt.NDArray[_typing.Any]:
    if type(values) is not _np.ndarray or values.ndim != 1:
        raise _ContractViolationError(f"{field} must be a one-dimensional NumPy array")
    if values.dtype != dtype or values.dtype.metadata is not None:
        raise _ContractViolationError(
            f"{field} must have exact metadata-free dtype {dtype.name}"
        )
    if length is not None and values.shape[0] != length:
        raise _ContractViolationError(
            f"{field} length {values.shape[0]} does not match required length {length}"
        )
    return values


@_dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class SimulationRequest:
    """Platform-minted immutable OHLC execution axis for exactly one fold."""

    _keys: _ImmutableArrayStorage
    _unit: _TimeUnit
    _clock_domain: _ClockDomain
    _source_identities: tuple[_DatasetIdentity, ...]
    _open: _ImmutableArrayStorage
    _high: _ImmutableArrayStorage
    _low: _ImmutableArrayStorage
    _close: _ImmutableArrayStorage

    def __init__(self) -> None:
        """Refuse direct construction; only the experiment platform mints requests."""
        raise TypeError("SimulationRequest instances are issued only by the experiment platform")

    @property
    def keys(self) -> _npt.NDArray[_np.int64]:
        """Return the strictly increasing execution keys as a fresh descriptor."""
        return _typing.cast("_npt.NDArray[_np.int64]", self._keys._descriptor())

    @property
    def unit(self) -> _TimeUnit:
        """Return the native-key resolution."""
        return self._unit

    @property
    def clock_domain(self) -> _ClockDomain:
        """Return the opaque native clock domain."""
        return self._clock_domain

    @property
    def num_rows(self) -> int:
        """Return the execution-row count."""
        return self._keys._length

    @property
    def source_identities(self) -> tuple[_DatasetIdentity, ...]:
        """Return ordered contributing dataset identities."""
        return self._source_identities

    @property
    def open(self) -> _npt.NDArray[_np.float64]:
        """Return immutable recorded opens for the declared price model."""
        return _typing.cast("_npt.NDArray[_np.float64]", self._open._descriptor())

    @property
    def high(self) -> _npt.NDArray[_np.float64]:
        """Return immutable recorded highs for the declared price model."""
        return _typing.cast("_npt.NDArray[_np.float64]", self._high._descriptor())

    @property
    def low(self) -> _npt.NDArray[_np.float64]:
        """Return immutable recorded lows for the declared price model."""
        return _typing.cast("_npt.NDArray[_np.float64]", self._low._descriptor())

    @property
    def close(self) -> _npt.NDArray[_np.float64]:
        """Return immutable recorded closes for the declared price model."""
        return _typing.cast("_npt.NDArray[_np.float64]", self._close._descriptor())


def _make_simulation_request(
    *,
    keys: _npt.NDArray[_typing.Any],
    unit: _TimeUnit,
    clock_domain: _ClockDomain,
    source_identities: tuple[_DatasetIdentity, ...],
    open_values: _npt.NDArray[_typing.Any],
    high_values: _npt.NDArray[_typing.Any],
    low_values: _npt.NDArray[_typing.Any],
    close_values: _npt.NDArray[_typing.Any],
) -> SimulationRequest:
    """Mint one request from arrays already bounded and authorized by experiments."""
    _validate_axis_tags(
        unit=unit,
        clock_domain=clock_domain,
        source_identities=source_identities,
    )
    validated_keys = _typing.cast(
        "_npt.NDArray[_np.int64]",
        _validated_array(keys, field="keys", dtype=_np.dtype(_np.int64)),
    )
    if validated_keys.size < 2:
        raise _ContractViolationError("SimulationRequest requires at least two observed rows")
    if _np.any(validated_keys[1:] <= validated_keys[:-1]):
        raise _ContractViolationError("SimulationRequest keys must be strictly increasing")

    length = int(validated_keys.size)
    columns: dict[str, _npt.NDArray[_np.float64]] = {}
    for name, values in (
        ("open", open_values),
        ("high", high_values),
        ("low", low_values),
        ("close", close_values),
    ):
        column = _typing.cast(
            "_npt.NDArray[_np.float64]",
            _validated_array(
                values,
                field=name,
                dtype=_np.dtype(_np.float64),
                length=length,
            ),
        )
        if not _np.isfinite(column).all():
            raise _ContractViolationError(f"SimulationRequest {name} contains a non-finite value")
        columns[name] = column

    if _np.any(columns["low"] > columns["high"]):
        raise _ContractViolationError("SimulationRequest contains low greater than high")
    if _np.any(columns["low"] > _np.minimum(columns["open"], columns["close"])):
        raise _ContractViolationError("SimulationRequest contains open or close below low")
    if _np.any(columns["high"] < _np.maximum(columns["open"], columns["close"])):
        raise _ContractViolationError("SimulationRequest contains open or close above high")

    request = object.__new__(SimulationRequest)
    object.__setattr__(request, "_keys", _ImmutableArrayStorage._from_array(validated_keys))
    object.__setattr__(request, "_unit", unit)
    object.__setattr__(request, "_clock_domain", clock_domain)
    object.__setattr__(request, "_source_identities", source_identities)
    object.__setattr__(request, "_open", _ImmutableArrayStorage._from_array(columns["open"]))
    object.__setattr__(request, "_high", _ImmutableArrayStorage._from_array(columns["high"]))
    object.__setattr__(request, "_low", _ImmutableArrayStorage._from_array(columns["low"]))
    object.__setattr__(request, "_close", _ImmutableArrayStorage._from_array(columns["close"]))
    return request


_TRADE_LOG_DTYPES = _MappingProxyType(
    {
        "entry_key": _np.dtype(_np.int64),
        "exit_key": _np.dtype(_np.int64),
        "direction": _np.dtype(_np.int8),
        "entry_price": _np.dtype(_np.float64),
        "exit_price": _np.dtype(_np.float64),
        "exit_reason": _np.dtype(_np.int8),
        "ambiguous_exit": _np.dtype(_np.bool_),
        "gapped_exit": _np.dtype(_np.bool_),
        "gross_pnl": _np.dtype(_np.float64),
        "spread_cost": _np.dtype(_np.float64),
        "commission_cost": _np.dtype(_np.float64),
        "net_pnl": _np.dtype(_np.float64),
        "take_profit_price": _np.dtype(_np.float64),
        "stop_loss_price": _np.dtype(_np.float64),
    }
)
_TRADE_LOG_FLOAT_COLUMNS = (
    "entry_price",
    "exit_price",
    "gross_pnl",
    "spread_cost",
    "commission_cost",
    "net_pnl",
)
# The recorded bracket levels are the one place a NaN is legal in a TradeLog
# float column: it states that this trade had no bracket on that side. They are
# therefore validated separately from the finite-only columns above.
_TRADE_LOG_BRACKET_COLUMNS = (
    "take_profit_price",
    "stop_loss_price",
)


@_dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class TradeLog:
    """Immutable chronological completed-trade artifact for one fold and policy."""

    _columns: _Mapping[str, _ImmutableArrayStorage]
    _unit: _TimeUnit
    _clock_domain: _ClockDomain
    _source_identities: tuple[_DatasetIdentity, ...]
    _fold_index: int
    _policy_origin: _StageOrigin
    _intent_schema_id: str
    _execution_spec: ExecutionSpec
    _execution_rows: int
    _intent_rows_long: int
    _intent_rows_flat: int
    _intent_rows_short: int
    _intent_rows_abstain: int
    _intent_rows_absent: int
    _unexecuted_final_row_intents: int

    def __init__(self) -> None:
        """Refuse direct construction; only the simulator mints trade logs."""
        raise TypeError("TradeLog instances are issued only by the simulation platform")

    def _column(self, name: str) -> _npt.NDArray[_typing.Any]:
        return self._columns[name]._descriptor()

    @property
    def entry_key(self) -> _npt.NDArray[_np.int64]:
        """Return observed entry-fill keys."""
        return _typing.cast("_npt.NDArray[_np.int64]", self._column("entry_key"))

    @property
    def exit_key(self) -> _npt.NDArray[_np.int64]:
        """Return observed exit-fill keys."""
        return _typing.cast("_npt.NDArray[_np.int64]", self._column("exit_key"))

    @property
    def direction(self) -> _npt.NDArray[_np.int8]:
        """Return +1 LONG and -1 SHORT codes."""
        return _typing.cast("_npt.NDArray[_np.int8]", self._column("direction"))

    @property
    def entry_price(self) -> _npt.NDArray[_np.float64]:
        """Return entry fill prices."""
        return _typing.cast("_npt.NDArray[_np.float64]", self._column("entry_price"))

    @property
    def exit_price(self) -> _npt.NDArray[_np.float64]:
        """Return exit fill prices."""
        return _typing.cast("_npt.NDArray[_np.float64]", self._column("exit_price"))

    @property
    def exit_reason(self) -> _npt.NDArray[_np.int8]:
        """Return pinned TradeExitReason int8 codes."""
        return _typing.cast("_npt.NDArray[_np.int8]", self._column("exit_reason"))

    @property
    def ambiguous_exit(self) -> _npt.NDArray[_np.bool_]:
        """Return whether both brackets were touched and stop-loss won."""
        return _typing.cast("_npt.NDArray[_np.bool_]", self._column("ambiguous_exit"))

    @property
    def gapped_exit(self) -> _npt.NDArray[_np.bool_]:
        """Return whether the exit was priced from a gapped observed open."""
        return _typing.cast("_npt.NDArray[_np.bool_]", self._column("gapped_exit"))

    @property
    def gross_pnl(self) -> _npt.NDArray[_np.float64]:
        """Return gross account-currency PnL."""
        return _typing.cast("_npt.NDArray[_np.float64]", self._column("gross_pnl"))

    @property
    def spread_cost(self) -> _npt.NDArray[_np.float64]:
        """Return configured spread money charges."""
        return _typing.cast("_npt.NDArray[_np.float64]", self._column("spread_cost"))

    @property
    def commission_cost(self) -> _npt.NDArray[_np.float64]:
        """Return configured commission money charges."""
        return _typing.cast("_npt.NDArray[_np.float64]", self._column("commission_cost"))

    @property
    def net_pnl(self) -> _npt.NDArray[_np.float64]:
        """Return gross PnL less spread and commission."""
        return _typing.cast("_npt.NDArray[_np.float64]", self._column("net_pnl"))

    @property
    def take_profit_price(self) -> _npt.NDArray[_np.float64]:
        """Return the exact take-profit level the simulator used, NaN when absent.

        This is the recorded level itself, not a reconstruction: it is the number
        the trade's high/low were compared against, whatever supplied it.
        """
        return _typing.cast("_npt.NDArray[_np.float64]", self._column("take_profit_price"))

    @property
    def stop_loss_price(self) -> _npt.NDArray[_np.float64]:
        """Return the exact stop-loss level the simulator used, NaN when absent."""
        return _typing.cast("_npt.NDArray[_np.float64]", self._column("stop_loss_price"))

    @property
    def unit(self) -> _TimeUnit:
        """Return the native-key resolution."""
        return self._unit

    @property
    def clock_domain(self) -> _ClockDomain:
        """Return the opaque native clock domain."""
        return self._clock_domain

    @property
    def source_identities(self) -> tuple[_DatasetIdentity, ...]:
        """Return ordered contributing dataset identities."""
        return self._source_identities

    @property
    def fold_index(self) -> int:
        """Return the zero-based producing fold index."""
        return self._fold_index

    @property
    def policy_origin(self) -> _StageOrigin:
        """Return the identity-bearing DecisionPolicy declaration."""
        return self._policy_origin

    @property
    def intent_schema_id(self) -> str:
        """Return the consumed intent schema identifier."""
        return self._intent_schema_id

    @property
    def execution_spec(self) -> ExecutionSpec:
        """Return the complete execution configuration."""
        return self._execution_spec

    @property
    def num_trades(self) -> int:
        """Return the completed-trade count."""
        return self._columns["entry_key"]._length

    @property
    def execution_rows(self) -> int:
        """Return the observed execution-axis row count."""
        return self._execution_rows

    @property
    def intent_rows_long(self) -> int:
        """Return explicit LONG intent-row count."""
        return self._intent_rows_long

    @property
    def intent_rows_flat(self) -> int:
        """Return explicit FLAT intent-row count."""
        return self._intent_rows_flat

    @property
    def intent_rows_short(self) -> int:
        """Return explicit SHORT intent-row count."""
        return self._intent_rows_short

    @property
    def intent_rows_abstain(self) -> int:
        """Return explicit NaN abstention-row count."""
        return self._intent_rows_abstain

    @property
    def intent_rows_absent(self) -> int:
        """Return execution rows at which the sparse frame made no statement."""
        return self._intent_rows_absent

    @property
    def unexecuted_final_row_intents(self) -> int:
        """Return explicit final-row intents that had no eligible next row."""
        return self._unexecuted_final_row_intents


def _validated_non_negative_int(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise _ContractViolationError(f"{field} must be a non-negative Python int")
    return value


def _validate_recorded_bracket_sides(
    raw_columns: _Mapping[str, _npt.NDArray[_typing.Any]],
) -> None:
    """Refuse a recorded bracket level that sits on the wrong side of its entry.

    A take profit is always beyond the entry in the direction of the trade and a
    stop loss is always behind it. Checking that at the contract boundary turns a
    whole class of sign error into a refused artifact rather than a plausible
    chart, and it costs one comparison per trade.
    """
    direction = raw_columns["direction"].astype(_np.float64)
    entry = raw_columns["entry_price"]
    take_profit = raw_columns["take_profit_price"]
    stop_loss = raw_columns["stop_loss_price"]

    take_profit_set = ~_np.isnan(take_profit)
    if _np.any(take_profit_set & (direction * (take_profit - entry) <= 0.0)):
        raise _ContractViolationError(
            "TradeLog take_profit_price must lie beyond entry_price in the trade direction"
        )
    stop_loss_set = ~_np.isnan(stop_loss)
    if _np.any(stop_loss_set & (direction * (entry - stop_loss) <= 0.0)):
        raise _ContractViolationError(
            "TradeLog stop_loss_price must lie behind entry_price in the trade direction"
        )


def _make_trade_log(
    *,
    columns: _Mapping[str, _npt.NDArray[_typing.Any]],
    unit: _TimeUnit,
    clock_domain: _ClockDomain,
    source_identities: tuple[_DatasetIdentity, ...],
    fold_index: int,
    policy_origin: _StageOrigin,
    intent_schema_id: str,
    execution_spec: ExecutionSpec,
    execution_rows: int,
    intent_rows_long: int,
    intent_rows_flat: int,
    intent_rows_short: int,
    intent_rows_abstain: int,
    intent_rows_absent: int,
    unexecuted_final_row_intents: int,
) -> TradeLog:
    """Mint one validated completed-trade artifact from simulator output."""
    _validate_axis_tags(
        unit=unit,
        clock_domain=clock_domain,
        source_identities=source_identities,
    )
    if type(fold_index) is not int or fold_index < 0:
        raise _ContractViolationError("fold_index must be a non-negative Python int")
    if type(policy_origin) is not _StageOrigin:
        raise _ContractViolationError("policy_origin must be an exact StageOrigin")
    if type(intent_schema_id) is not str or not intent_schema_id:
        raise _ContractViolationError("intent_schema_id must be a non-empty string")
    if type(execution_spec) is not ExecutionSpec:
        raise _ContractViolationError("execution_spec must be an exact ExecutionSpec")
    if type(execution_rows) is not int or execution_rows < 2:
        raise _ContractViolationError("execution_rows must be a Python int of at least two")
    if not isinstance(columns, _Mapping) or set(columns) != set(_TRADE_LOG_DTYPES):
        raise _ContractViolationError(
            "TradeLog columns must equal the closed fourteen-column schema"
        )

    raw_columns: dict[str, _npt.NDArray[_typing.Any]] = {}
    length: int | None = None
    for name, dtype in _TRADE_LOG_DTYPES.items():
        column = _validated_array(
            columns[name],
            field=name,
            dtype=_typing.cast("_np.dtype[_np.generic]", dtype),
            length=length,
        )
        if length is None:
            length = int(column.size)
        raw_columns[name] = column
    if length is None:
        raise AssertionError("the closed TradeLog schema cannot be empty")

    for name in _TRADE_LOG_FLOAT_COLUMNS:
        if not _np.isfinite(raw_columns[name]).all():
            raise _ContractViolationError(f"TradeLog {name} contains a non-finite value")
    for name in _TRADE_LOG_BRACKET_COLUMNS:
        column = raw_columns[name]
        if _np.isinf(column).any():
            raise _ContractViolationError(f"TradeLog {name} contains an infinite value")
    _validate_recorded_bracket_sides(raw_columns)
    if _np.any(raw_columns["spread_cost"] < 0.0) or _np.any(
        raw_columns["commission_cost"] < 0.0
    ):
        raise _ContractViolationError("TradeLog costs must be non-negative")
    if _np.any(raw_columns["entry_key"] > raw_columns["exit_key"]):
        raise _ContractViolationError("TradeLog entry_key must not follow exit_key")
    if length > 1 and _np.any(
        raw_columns["entry_key"][1:] < raw_columns["entry_key"][:-1]
    ):
        raise _ContractViolationError("TradeLog rows must be ordered by entry_key")
    if length and not _np.isin(raw_columns["direction"], (-1, 1)).all():
        raise _ContractViolationError("TradeLog direction must contain only -1 or +1")
    legal_exit_codes = tuple(_TRADE_EXIT_REASON_CODES.values())
    if length and not _np.isin(raw_columns["exit_reason"], legal_exit_codes).all():
        raise _ContractViolationError("TradeLog exit_reason contains an unknown int8 code")

    ambiguous = _typing.cast("_npt.NDArray[_np.bool_]", raw_columns["ambiguous_exit"])
    gapped = _typing.cast("_npt.NDArray[_np.bool_]", raw_columns["gapped_exit"])
    reasons = raw_columns["exit_reason"]
    if _np.any(ambiguous & gapped):
        raise _ContractViolationError("a TradeLog exit cannot be both ambiguous and gapped")
    if _np.any(ambiguous & (reasons != _TRADE_EXIT_REASON_CODES[TradeExitReason.STOP_LOSS])):
        raise _ContractViolationError("an ambiguous TradeLog exit must be STOP_LOSS")
    bracket_codes = (
        _TRADE_EXIT_REASON_CODES[TradeExitReason.TAKE_PROFIT],
        _TRADE_EXIT_REASON_CODES[TradeExitReason.STOP_LOSS],
    )
    if _np.any(gapped & ~_np.isin(reasons, bracket_codes)):
        raise _ContractViolationError("a gapped TradeLog exit must be TAKE_PROFIT or STOP_LOSS")

    expected_net = (
        raw_columns["gross_pnl"]
        - raw_columns["spread_cost"]
        - raw_columns["commission_cost"]
    )
    if not _np.array_equal(raw_columns["net_pnl"], expected_net):
        raise _ContractViolationError(
            "TradeLog net_pnl must equal gross_pnl - spread_cost - commission_cost"
        )

    counter_names = (
        "intent_rows_long",
        "intent_rows_flat",
        "intent_rows_short",
        "intent_rows_abstain",
        "intent_rows_absent",
    )
    supplied_counters = (
        intent_rows_long,
        intent_rows_flat,
        intent_rows_short,
        intent_rows_abstain,
        intent_rows_absent,
    )
    counter_values = tuple(
        _validated_non_negative_int(value, field=name)
        for name, value in zip(counter_names, supplied_counters, strict=True)
    )
    unexecuted = _validated_non_negative_int(
        unexecuted_final_row_intents,
        field="unexecuted_final_row_intents",
    )
    if sum(counter_values) != execution_rows:
        raise _ContractViolationError(
            "intent row counters must partition the complete execution axis"
        )
    if unexecuted > 1 or (unexecuted and sum(counter_values[:-1]) == 0):
        raise _ContractViolationError(
            "unexecuted_final_row_intents must be zero or one and require an explicit row"
        )

    log = object.__new__(TradeLog)
    object.__setattr__(
        log,
        "_columns",
        _MappingProxyType(
            {
                name: _ImmutableArrayStorage._from_array(raw_columns[name])
                for name in _TRADE_LOG_DTYPES
            }
        ),
    )
    object.__setattr__(log, "_unit", unit)
    object.__setattr__(log, "_clock_domain", clock_domain)
    object.__setattr__(log, "_source_identities", source_identities)
    object.__setattr__(log, "_fold_index", fold_index)
    object.__setattr__(log, "_policy_origin", policy_origin)
    object.__setattr__(log, "_intent_schema_id", intent_schema_id)
    object.__setattr__(log, "_execution_spec", execution_spec)
    object.__setattr__(log, "_execution_rows", execution_rows)
    object.__setattr__(log, "_intent_rows_long", intent_rows_long)
    object.__setattr__(log, "_intent_rows_flat", intent_rows_flat)
    object.__setattr__(log, "_intent_rows_short", intent_rows_short)
    object.__setattr__(log, "_intent_rows_abstain", intent_rows_abstain)
    object.__setattr__(log, "_intent_rows_absent", intent_rows_absent)
    object.__setattr__(
        log,
        "_unexecuted_final_row_intents",
        unexecuted_final_row_intents,
    )
    return log


@_dataclass(frozen=True, slots=True, init=False, eq=False, repr=False)
class EquityCurve:
    """Dense cumulative net-PnL curve over one fold's execution axis."""

    _keys: _ImmutableArrayStorage
    _equity: _ImmutableArrayStorage
    _unit: _TimeUnit
    _clock_domain: _ClockDomain
    _source_identities: tuple[_DatasetIdentity, ...]
    _fold_index: int
    _policy_origin: _StageOrigin

    def __init__(self) -> None:
        """Refuse direct construction; only the simulator mints equity curves."""
        raise TypeError("EquityCurve instances are issued only by the simulation platform")

    @property
    def keys(self) -> _npt.NDArray[_np.int64]:
        """Return the dense execution keys as a fresh read-only descriptor."""
        return _typing.cast("_npt.NDArray[_np.int64]", self._keys._descriptor())

    @property
    def equity(self) -> _npt.NDArray[_np.float64]:
        """Return cumulative net PnL in account currency."""
        return _typing.cast("_npt.NDArray[_np.float64]", self._equity._descriptor())

    @property
    def unit(self) -> _TimeUnit:
        """Return the native-key resolution."""
        return self._unit

    @property
    def clock_domain(self) -> _ClockDomain:
        """Return the opaque native clock domain."""
        return self._clock_domain

    @property
    def source_identities(self) -> tuple[_DatasetIdentity, ...]:
        """Return ordered contributing dataset identities."""
        return self._source_identities

    @property
    def fold_index(self) -> int:
        """Return the zero-based producing fold index."""
        return self._fold_index

    @property
    def policy_origin(self) -> _StageOrigin:
        """Return the identity-bearing DecisionPolicy declaration."""
        return self._policy_origin

    @property
    def num_rows(self) -> int:
        """Return the dense execution-row count."""
        return self._keys._length


def _make_equity_curve(
    *,
    keys: _npt.NDArray[_typing.Any],
    equity: _npt.NDArray[_typing.Any],
    unit: _TimeUnit,
    clock_domain: _ClockDomain,
    source_identities: tuple[_DatasetIdentity, ...],
    fold_index: int,
    policy_origin: _StageOrigin,
) -> EquityCurve:
    """Mint one validated dense mark-to-market curve from simulator output."""
    _validate_axis_tags(
        unit=unit,
        clock_domain=clock_domain,
        source_identities=source_identities,
    )
    if type(fold_index) is not int or fold_index < 0:
        raise _ContractViolationError("fold_index must be a non-negative Python int")
    if type(policy_origin) is not _StageOrigin:
        raise _ContractViolationError("policy_origin must be an exact StageOrigin")
    validated_keys = _typing.cast(
        "_npt.NDArray[_np.int64]",
        _validated_array(keys, field="keys", dtype=_np.dtype(_np.int64)),
    )
    if validated_keys.size < 2:
        raise _ContractViolationError("EquityCurve requires at least two execution rows")
    if _np.any(validated_keys[1:] <= validated_keys[:-1]):
        raise _ContractViolationError("EquityCurve keys must be strictly increasing")
    validated_equity = _typing.cast(
        "_npt.NDArray[_np.float64]",
        _validated_array(
            equity,
            field="equity",
            dtype=_np.dtype(_np.float64),
            length=int(validated_keys.size),
        ),
    )
    if not _np.isfinite(validated_equity).all():
        raise _ContractViolationError("EquityCurve equity contains a non-finite value")

    curve = object.__new__(EquityCurve)
    object.__setattr__(curve, "_keys", _ImmutableArrayStorage._from_array(validated_keys))
    object.__setattr__(curve, "_equity", _ImmutableArrayStorage._from_array(validated_equity))
    object.__setattr__(curve, "_unit", unit)
    object.__setattr__(curve, "_clock_domain", clock_domain)
    object.__setattr__(curve, "_source_identities", source_identities)
    object.__setattr__(curve, "_fold_index", fold_index)
    object.__setattr__(curve, "_policy_origin", policy_origin)
    return curve
