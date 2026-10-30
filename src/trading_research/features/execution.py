"""Deterministic execution of bounded, column-isolated feature providers.

This module consumes only already-issued market frames. It performs no data
loading, authorization, persistence, configuration lookup, or wall-clock
measurement. The sole bounded-view adapter lives in ``experiments.features``.
"""

from __future__ import annotations

import typing as _typing
from collections.abc import Callable as _Callable
from collections.abc import Mapping as _Mapping
from collections.abc import Sequence as _Sequence
from dataclasses import dataclass as _dataclass

import numpy as _np

from trading_research.core.errors import FeatureContractError as _FeatureContractError
from trading_research.core.errors import FeatureFrameError as _FeatureFrameError
from trading_research.core.errors import FeatureRequestError as _FeatureRequestError
from trading_research.core.errors import InsufficientContextError as _InsufficientContextError
from trading_research.core.feature_frames import FeatureFrame as _FeatureFrame
from trading_research.core.feature_frames import _make_feature_frame
from trading_research.core.features import CostHint as _CostHint
from trading_research.core.features import FeatureOrigin as _FeatureOrigin
from trading_research.core.features import FeatureRequest as _FeatureRequest
from trading_research.core.features import GapPolicy as _GapPolicy
from trading_research.core.features import WindowSpec as _WindowSpec
from trading_research.core.features import canonical_params_json as _canonical_params_json

if _typing.TYPE_CHECKING:
    import numpy.typing as _npt

    from trading_research.core.features import ParamValue as _ParamValue
    from trading_research.core.frames import MarketFrame as _MarketFrame
    from trading_research.core.interfaces import FeatureProvider as _FeatureProvider
    from trading_research.core.time import NativeTimeKey as _NativeTimeKey

__all__: list[str] = []


@_dataclass(frozen=True, slots=True)
class _ProviderPlan:
    """One immutable snapshot of a provider's complete declaration."""

    _index: int
    _origin: _FeatureOrigin
    _feature_names: tuple[str, ...]
    _required_columns: tuple[str, ...]
    _cost_hint: _CostHint
    _compute: _Callable[[_FeatureRequest], object]

    @property
    def provider_id(self) -> str:
        """Stable provider id captured from the original declaration."""
        return self._origin.provider_id

    @property
    def version(self) -> int:
        """Captured provider contract version."""
        return self._origin.version

    @property
    def params(self) -> _Mapping[str, _ParamValue]:
        """Detached canonical provider parameters."""
        return self._origin.params

    @property
    def window(self) -> _WindowSpec:
        """Captured finite-reach declaration."""
        return self._origin.window

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Captured ordered output names."""
        return self._feature_names

    @property
    def required_columns(self) -> tuple[str, ...]:
        """Captured ordered readable market columns."""
        return self._required_columns

    @property
    def cost_hint(self) -> _CostHint:
        """Captured hint used by materialization without a clock."""
        return self._cost_hint

    def compute(
        self,
        request: _FeatureRequest,
    ) -> _Mapping[str, _npt.NDArray[_np.float64]]:
        """Invoke only the callable captured during plan preparation."""
        return _typing.cast(
            "_Mapping[str, _npt.NDArray[_np.float64]]",
            self._compute(request),
        )


class _ProviderExecutionError(Exception):
    """Private signal identifying the provider whose invocation failed."""

    __slots__ = ("_error", "provider_id")

    def __init__(self, provider_id: str, error: Exception) -> None:
        super().__init__(f"feature provider {provider_id!r} failed")
        self.provider_id = provider_id
        self._error = error


def _execute_features(
    *,
    frames: _Sequence[_MarketFrame],
    eval_start: _NativeTimeKey,
    eval_end: _NativeTimeKey,
    expected_eval_keys: _npt.NDArray[_np.int64],
    providers: _Sequence[_FeatureProvider],
    short_context_accepted: bool,
) -> _FeatureFrame:
    """Validate a full plan, execute it, mask unavailable rows, and assemble."""
    plans = _validated_plan(providers)
    return _execute_feature_plans(
        frames=frames,
        eval_start=eval_start,
        eval_end=eval_end,
        expected_eval_keys=expected_eval_keys,
        plans=plans,
        short_context_accepted=short_context_accepted,
        capture_provider_failure=False,
    )


def _execute_feature_plans(
    *,
    frames: _Sequence[_MarketFrame],
    eval_start: _NativeTimeKey,
    eval_end: _NativeTimeKey,
    expected_eval_keys: _npt.NDArray[_np.int64],
    plans: tuple[_ProviderPlan, ...],
    short_context_accepted: bool,
    capture_provider_failure: bool,
) -> _FeatureFrame:
    """Execute one already-snapshotted provider plan without rereading providers."""
    if type(short_context_accepted) is not bool:
        raise _FeatureContractError("short-context acceptance must be an exact bool")
    expected_keys = _validated_expected_keys(expected_eval_keys)

    requests: list[_FeatureRequest] = []
    for plan in plans:
        try:
            request = _FeatureRequest.from_segments(
                frames,
                eval_start=eval_start,
                eval_end=eval_end,
                required_columns=plan._required_columns,
            )
        except _FeatureRequestError as exc:
            error = _FeatureRequestError(
                f"provider {plan._origin.provider_id!r} cannot receive its declared input: {exc}"
            )
            if capture_provider_failure:
                raise _ProviderExecutionError(plan.provider_id, error) from exc
            raise error from exc
        requests.append(request)

    for plan, request in zip(plans, requests, strict=True):
        request_eval_keys = request.keys[
            request.eval_offset : request.eval_offset + request.eval_length
        ]
        if not _np.array_equal(request_eval_keys, expected_keys):
            alignment_error = _FeatureRequestError(
                f"provider {plan._origin.provider_id!r} evaluation keys do not match "
                "the bounded view evaluation frame"
            )
            if capture_provider_failure:
                raise _ProviderExecutionError(
                    plan.provider_id, alignment_error
                ) from alignment_error
            raise alignment_error
        if request.eval_offset < plan._origin.window.max_prior_rows and not short_context_accepted:
            context_error = _InsufficientContextError(
                f"provider {plan._origin.provider_id!r} requires "
                f"{plan._origin.window.max_prior_rows} prior observed rows but only "
                f"{request.eval_offset} are available"
            )
            if capture_provider_failure:
                raise _ProviderExecutionError(plan.provider_id, context_error) from context_error
            raise context_error

    columns: dict[str, _npt.NDArray[_np.float64]] = {}
    origins: dict[str, _FeatureOrigin] = {}
    for plan, request in zip(plans, requests, strict=True):
        try:
            emitted = plan._compute(request)
            validated = _validated_output(plan=plan, request=request, emitted=emitted)
            unavailable = _unavailable_mask(request=request, window=plan._origin.window)
        except Exception as exc:
            if capture_provider_failure:
                raise _ProviderExecutionError(plan.provider_id, exc) from exc
            raise
        for name in plan._feature_names:
            values = validated[name]
            values[unavailable] = _np.nan
            columns[name] = values
            origins[name] = plan._origin

    return _make_feature_frame(
        request=requests[0],
        columns=columns,
        origins=origins,
    )


def _validate_prepared_context(
    *,
    frames: _Sequence[_MarketFrame],
    eval_start: _NativeTimeKey,
    expected_eval_keys: _npt.NDArray[_np.int64],
    plans: tuple[_ProviderPlan, ...],
    short_context_accepted: bool,
    capture_provider_failure: bool,
) -> None:
    """Enforce reach refusal before cache access without reading market columns."""
    if type(short_context_accepted) is not bool:
        raise _FeatureContractError("short-context acceptance must be an exact bool")
    _validated_expected_keys(expected_eval_keys)
    if not frames:
        raise _FeatureRequestError("at least one bounded market frame is required")
    final_keys = frames[-1].keys
    final_prior_rows = int(_np.searchsorted(final_keys, eval_start.value, side="left"))
    available_prior_rows = sum(frame.num_rows for frame in frames[:-1]) + final_prior_rows
    for plan in plans:
        reach = plan._origin.window.max_prior_rows
        if available_prior_rows < reach and not short_context_accepted:
            error = _InsufficientContextError(
                f"provider {plan.provider_id!r} requires {reach} prior observed rows "
                f"but only {available_prior_rows} are available"
            )
            if capture_provider_failure:
                raise _ProviderExecutionError(plan.provider_id, error) from error
            raise error


def _validated_plan(providers: object) -> tuple[_ProviderPlan, ...]:
    """Snapshot every declaration and reject the whole plan before compute."""
    provider_objects = _validated_provider_objects(providers)
    return _validated_plan_from_objects(provider_objects)


def _validated_provider_objects(providers: object) -> tuple[object, ...]:
    """Consume one ordered provider sequence exactly once."""
    if isinstance(providers, str | bytes) or not isinstance(providers, _Sequence):
        raise _FeatureContractError("providers must be an ordered sequence")
    provider_objects = tuple(providers)
    if not provider_objects:
        raise _FeatureContractError("at least one feature provider is required")
    return provider_objects


def _validated_plan_from_objects(
    provider_objects: tuple[object, ...],
) -> tuple[_ProviderPlan, ...]:
    """Snapshot declarations from one already-detached provider sequence."""
    plans = tuple(
        _snapshot_provider(provider, index=index) for index, provider in enumerate(provider_objects)
    )
    owners: dict[str, tuple[str, int]] = {}
    for plan in plans:
        provider_id = plan._origin.provider_id
        for feature_name in plan._feature_names:
            prior = owners.get(feature_name)
            if prior is not None:
                prior_id, prior_index = prior
                raise _FeatureContractError(
                    f"feature name {feature_name!r} is declared by both provider "
                    f"{prior_id!r} at index {prior_index} and provider "
                    f"{provider_id!r} at index {plan._index}"
                )
            owners[feature_name] = (provider_id, plan._index)
    return plans


def _revalidated_provider_plan(
    *,
    provider_objects: tuple[object, ...],
    expected: tuple[_ProviderPlan, ...],
) -> tuple[_ProviderPlan, ...]:
    """Refuse declaration mutation across a callback-capable cache lookup."""
    observed = _validated_plan_from_objects(provider_objects)
    if len(observed) != len(expected):
        raise _FeatureContractError("provider plan changed after identity construction")
    for before, after in zip(expected, observed, strict=True):
        if not _same_provider_declaration(before, after):
            raise _FeatureContractError(
                f"provider {before.provider_id!r} declaration changed after identity construction"
            )
    return observed


def _same_provider_declaration(left: _ProviderPlan, right: _ProviderPlan) -> bool:
    return (
        left._index == right._index
        and left.provider_id == right.provider_id
        and left.version == right.version
        and _canonical_params_json(left.params) == _canonical_params_json(right.params)
        and left.window == right.window
        and left.feature_names == right.feature_names
        and left.required_columns == right.required_columns
        and left.cost_hint is right.cost_hint
    )


def _snapshot_provider(provider: object, *, index: int) -> _ProviderPlan:
    """Read each provider declaration once and make its identity immutable."""
    label = f"provider at index {index}"
    provider_id = _read_attribute(provider, "provider_id", label=label)
    if type(provider_id) is not str or not provider_id:
        raise _FeatureContractError(f"{label} provider_id must be a non-empty string")
    label = f"provider {provider_id!r}"

    version = _read_attribute(provider, "version", label=label)
    if type(version) is not int:
        raise _FeatureContractError(f"{label} version must be a Python int")
    feature_names = _validated_names(
        _read_attribute(provider, "feature_names", label=label),
        field="feature_names",
        provider_id=provider_id,
        allow_empty=False,
    )
    required_columns = _validated_names(
        _read_attribute(provider, "required_columns", label=label),
        field="required_columns",
        provider_id=provider_id,
        allow_empty=True,
    )
    window = _read_attribute(provider, "window", label=label)
    if type(window) is not _WindowSpec:
        raise _FeatureContractError(
            f"{label} window must be an exact WindowSpec, got {type(window).__name__}"
        )
    params = _read_attribute(provider, "params", label=label)
    if not isinstance(params, _Mapping):
        raise _FeatureContractError(
            f"{label} params must be a mapping, got {type(params).__name__}"
        )
    origin = _FeatureOrigin(
        provider_id=provider_id,
        version=version,
        params=params,
        window=window,
    )
    cost_hint = _read_attribute(provider, "cost_hint", label=label)
    if type(cost_hint) is not _CostHint:
        raise _FeatureContractError(
            f"{label} cost_hint must be an exact CostHint, got {type(cost_hint).__name__}"
        )
    compute = _read_attribute(provider, "compute", label=label)
    if not callable(compute):
        raise _FeatureContractError(f"{label} compute must be callable")

    return _ProviderPlan(
        _index=index,
        _origin=origin,
        _feature_names=feature_names,
        _required_columns=required_columns,
        _cost_hint=cost_hint,
        _compute=compute,
    )


def _read_attribute(provider: object, name: str, *, label: str) -> object:
    try:
        return getattr(provider, name)
    except Exception as exc:
        raise _FeatureContractError(f"{label} declaration {name!r} could not be read") from exc


def _validated_names(
    names: object,
    *,
    field: str,
    provider_id: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    if type(names) is not tuple:
        raise _FeatureContractError(f"provider {provider_id!r} {field} must be an exact tuple")
    if not names and not allow_empty:
        raise _FeatureContractError(
            f"provider {provider_id!r} must declare at least one feature name"
        )
    if any(type(name) is not str or not name for name in names):
        raise _FeatureContractError(
            f"provider {provider_id!r} {field} must contain only non-empty strings"
        )
    if len(set(names)) != len(names):
        raise _FeatureContractError(f"provider {provider_id!r} {field} contains duplicate names")
    for name in names:
        if _is_reserved_target_name(name):
            raise _FeatureContractError(
                f"provider {provider_id!r} {field} contains reserved name {name!r}; "
                "V1 reserves exactly 'target' and the 'target.' namespace"
            )
    return names


def _is_reserved_target_name(name: str) -> bool:
    """Return whether ``name`` is exactly in the owner-approved V1 namespace."""
    return name == "target" or name.startswith("target.")


def _validated_expected_keys(
    keys: object,
) -> _npt.NDArray[_np.int64]:
    if type(keys) is not _np.ndarray:
        raise _FeatureRequestError("bounded-view evaluation keys must be a NumPy array")
    if keys.ndim != 1:
        raise _FeatureRequestError("bounded-view evaluation keys must be one-dimensional")
    if keys.dtype != _np.dtype(_np.int64) or keys.dtype.metadata is not None:
        raise _FeatureRequestError(
            "bounded-view evaluation keys must have exact metadata-free dtype int64"
        )
    if not keys.size:
        raise _FeatureRequestError("bounded-view evaluation keys must not be empty")
    return _typing.cast("_npt.NDArray[_np.int64]", keys)


def _validated_output(
    *,
    plan: _ProviderPlan,
    request: _FeatureRequest,
    emitted: object,
) -> dict[str, _npt.NDArray[_np.float64]]:
    provider_id = plan._origin.provider_id
    if not isinstance(emitted, _Mapping):
        raise _FeatureFrameError(
            f"provider {provider_id!r} output must be a mapping of feature arrays"
        )
    emitted_names = tuple(emitted)
    if any(type(name) is not str or not name for name in emitted_names):
        raise _FeatureFrameError(f"provider {provider_id!r} output names must be non-empty strings")
    expected = set(plan._feature_names)
    observed = set(emitted_names)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise _FeatureFrameError(
            f"provider {provider_id!r} output names differ from its declaration; "
            f"missing={missing}, extra={extra}"
        )

    validated: dict[str, _npt.NDArray[_np.float64]] = {}
    for name in plan._feature_names:
        values = emitted[name]
        if type(values) is not _np.ndarray:
            raise _FeatureFrameError(
                f"provider {provider_id!r} feature {name!r} must be a NumPy array"
            )
        if values.ndim != 1:
            raise _FeatureFrameError(
                f"provider {provider_id!r} feature {name!r} must be one-dimensional"
            )
        if values.dtype != _np.dtype(_np.float64) or values.dtype.metadata is not None:
            raise _FeatureFrameError(
                f"provider {provider_id!r} feature {name!r} must have exact "
                "metadata-free dtype float64"
            )
        if values.shape[0] != request.eval_length:
            raise _FeatureFrameError(
                f"provider {provider_id!r} feature {name!r} length {values.shape[0]} "
                f"does not match evaluation length {request.eval_length}"
            )
        if _np.isinf(values).any():
            raise _FeatureFrameError(
                f"provider {provider_id!r} feature {name!r} contains positive or negative infinity"
            )
        validated[name] = values.copy(order="C")
    return validated


def _unavailable_mask(
    *,
    request: _FeatureRequest,
    window: _WindowSpec,
) -> _npt.NDArray[_np.bool_]:
    """Compute platform-owned warm-up and arithmetic-gap unavailability."""
    reach = window.max_prior_rows
    eval_stop = request.eval_offset + request.eval_length
    if reach >= eval_stop:
        return _np.ones(request.eval_length, dtype=_np.bool_)
    eval_positions = _np.arange(
        request.eval_offset,
        eval_stop,
        dtype=_np.int64,
    )
    unavailable = eval_positions < reach
    if window.gap_policy is _GapPolicy.CROSS or reach == 0:
        return unavailable

    interval = window.nominal_interval
    if interval is None:
        raise AssertionError("validated BREAK window has no nominal interval")
    keys = request.keys
    if keys.size > 1 and _np.any(keys[1:] <= keys[:-1]):
        raise _FeatureContractError("feature-window keys must be strictly increasing and unique")
    unsigned_keys = keys.view(_np.uint64)
    deltas = unsigned_keys[1:] - unsigned_keys[:-1]
    break_at = _np.zeros(request.keys.shape[0], dtype=_np.int64)
    break_at[1:] = deltas != _np.uint64(interval)
    prefix = _np.cumsum(break_at, dtype=_np.int64)
    eligible = eval_positions >= reach
    positions = eval_positions[eligible]
    unavailable[eligible] |= prefix[positions] > prefix[positions - reach]
    return unavailable
