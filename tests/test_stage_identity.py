"""Slice 2D pure deterministic feature-stage identity."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections import OrderedDict
from dataclasses import FrozenInstanceError, dataclass, fields
from typing import TYPE_CHECKING, cast

import pytest

from trading_research.artifacts import PlatformIdentity, StageIdentity
from trading_research.core import (
    ClockDomain,
    ClockDomainError,
    ContractViolationError,
    CostHint,
    DatasetIdentity,
    DatasetRef,
    FeatureContractError,
    FeatureRequest,
    GapPolicy,
    NativeTimeKey,
    ParamValue,
    TimeUnit,
    TimeUnitMismatchError,
    ViewRole,
    WindowSpec,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    import numpy as np
    import numpy.typing as npt

_DOMAIN = ClockDomain("stage-identity.wallclock")
_POLICY_DIGEST = "c" * 64
_COMMIT = "d" * 40


@dataclass
class _Provider:
    provider_id: str
    version: int
    feature_names: tuple[str, ...]
    required_columns: tuple[str, ...]
    window: WindowSpec
    params: Mapping[str, ParamValue]
    cost_hint: CostHint = CostHint.CHEAP

    def compute(
        self,
        request: FeatureRequest,
    ) -> Mapping[str, npt.NDArray[np.float64]]:
        raise AssertionError(f"identity construction called compute with {request!r}")


class _AlternateProvider(_Provider):
    def compute(
        self,
        request: FeatureRequest,
    ) -> Mapping[str, npt.NDArray[np.float64]]:
        raise RuntimeError(f"a different implementation was called with {request!r}")


def _provider(
    *,
    provider_id: str = "alpha",
    version: int = 2,
    feature_names: tuple[str, ...] = ("alpha.one", "alpha.two"),
    required_columns: tuple[str, ...] = ("close", "high"),
    window: WindowSpec | None = None,
    params: Mapping[str, ParamValue] | None = None,
    cost_hint: CostHint = CostHint.CHEAP,
) -> _Provider:
    return _Provider(
        provider_id=provider_id,
        version=version,
        feature_names=feature_names,
        required_columns=required_columns,
        window=(
            WindowSpec(max_prior_rows=3, gap_policy=GapPolicy.CROSS) if window is None else window
        ),
        params=({"flags": (True, "β"), "threshold": 1.5} if params is None else params),
        cost_hint=cost_hint,
    )


def _second_provider() -> _Provider:
    return _provider(
        provider_id="beta",
        version=1,
        feature_names=("beta.value",),
        required_columns=("low",),
        window=WindowSpec(max_prior_rows=0, gap_policy=GapPolicy.CROSS),
        params={},
    )


def _sources() -> tuple[DatasetIdentity, ...]:
    return (
        DatasetIdentity("SYNTH_CONTEXT", "a" * 64),
        DatasetIdentity("SYNTH_EVALUATION", "b" * 64),
    )


def _key(
    value: int,
    *,
    unit: TimeUnit = TimeUnit.SECONDS,
    domain: ClockDomain = _DOMAIN,
) -> NativeTimeKey:
    return NativeTimeKey(value, unit, domain)


def _identity(
    *,
    source_identities: Sequence[DatasetIdentity] | None = None,
    resolved_context_start: NativeTimeKey | None = None,
    eval_start: NativeTimeKey | None = None,
    eval_end: NativeTimeKey | None = None,
    split_policy_adr: str = "ADR-0012",
    split_policy_version: str = "v1",
    split_policy_sha256: str = _POLICY_DIGEST,
    providers: Sequence[_Provider] | None = None,
    platform_identity: PlatformIdentity | None = None,
) -> StageIdentity:
    return StageIdentity(
        source_identities=_sources() if source_identities is None else source_identities,
        resolved_context_start=(
            _key(10) if resolved_context_start is None else resolved_context_start
        ),
        eval_start=_key(20) if eval_start is None else eval_start,
        eval_end=_key(30) if eval_end is None else eval_end,
        split_policy_adr=split_policy_adr,
        split_policy_version=split_policy_version,
        split_policy_sha256=split_policy_sha256,
        providers=(_provider(), _second_provider()) if providers is None else providers,
        platform_identity=(
            PlatformIdentity(_COMMIT, False, None)
            if platform_identity is None
            else platform_identity
        ),
    )


def _identity_key(**changes: object) -> str:
    return _identity(**changes).identity_key()  # type: ignore[arg-type]


def test_canonical_document_has_one_independent_fixed_golden_key() -> None:
    document = {
        "source_identities": [
            {"dataset_id": "SYNTH_CONTEXT", "manifest_sha256": "a" * 64},
            {"dataset_id": "SYNTH_EVALUATION", "manifest_sha256": "b" * 64},
        ],
        "bounds": {
            "resolved_context_start": 10,
            "eval_start": 20,
            "eval_end": 30,
            "time_unit": "seconds",
            "clock_domain": "stage-identity.wallclock",
        },
        "split_policy": {
            "adr": "ADR-0012",
            "policy_version": "v1",
            "policy_sha256": "c" * 64,
        },
        "providers": [
            {
                "provider_id": "alpha",
                "version": 2,
                "params": {
                    "flags": [True, "β"],
                    "threshold": {"__float_hex__": "0x1.8000000000000p+0"},
                },
                "feature_names": ["alpha.one", "alpha.two"],
                "required_columns": ["close", "high"],
                "window": {
                    "max_prior_rows": 3,
                    "gap_policy": "cross",
                    "nominal_interval": None,
                },
            },
            {
                "provider_id": "beta",
                "version": 1,
                "params": {},
                "feature_names": ["beta.value"],
                "required_columns": ["low"],
                "window": {
                    "max_prior_rows": 0,
                    "gap_policy": "cross",
                    "nominal_interval": None,
                },
            },
        ],
        "platform": {
            "git_commit": "d" * 40,
            "git_dirty": False,
            "diff_sha256": None,
        },
    }
    canonical = json.dumps(
        document,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    assert canonical.isascii()
    expected = "b62584f8fbc0a7c6f9a08589639b887a20791e07f3fc3606f04291d4eec562f3"
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == expected
    assert _identity().identity_key() == expected
    assert re.fullmatch(r"[0-9a-f]{64}", _identity().identity_key())


def test_every_source_identity_component_and_order_changes_the_key() -> None:
    base = _identity_key()
    variations = (
        (DatasetIdentity("OTHER_CONTEXT", "a" * 64), _sources()[1]),
        (DatasetIdentity("SYNTH_CONTEXT", "e" * 64), _sources()[1]),
        (_sources()[0],),
        tuple(reversed(_sources())),
    )
    assert all(_identity_key(source_identities=value) != base for value in variations)


def test_every_bound_axis_and_split_policy_component_changes_the_key() -> None:
    base = _identity_key()
    other_domain = ClockDomain("other.wallclock")
    variations = (
        {"resolved_context_start": _key(9)},
        {"eval_start": _key(21)},
        {"eval_end": _key(31)},
        {
            "resolved_context_start": _key(10, unit=TimeUnit.MILLISECONDS),
            "eval_start": _key(20, unit=TimeUnit.MILLISECONDS),
            "eval_end": _key(30, unit=TimeUnit.MILLISECONDS),
        },
        {
            "resolved_context_start": _key(10, domain=other_domain),
            "eval_start": _key(20, domain=other_domain),
            "eval_end": _key(30, domain=other_domain),
        },
        {"split_policy_adr": "ADR-OTHER"},
        {"split_policy_version": "v2"},
        {"split_policy_sha256": "e" * 64},
    )
    keys = {_identity_key(**variation) for variation in variations}
    assert base not in keys
    assert len(keys) == len(variations)


def test_every_provider_declaration_component_and_order_changes_the_key() -> None:
    base = _identity_key()
    alpha = _provider()
    beta = _second_provider()
    variations = (
        (_provider(provider_id="other"), beta),
        (_provider(version=3), beta),
        (_provider(params={"flags": (True, "β"), "threshold": 2.5}), beta),
        (_provider(params={"other": 1.5}), beta),
        (_provider(feature_names=("alpha.two", "alpha.one")), beta),
        (_provider(feature_names=("alpha.one", "alpha.changed")), beta),
        (_provider(required_columns=("high", "close")), beta),
        (_provider(required_columns=("close", "low")), beta),
        (
            _provider(window=WindowSpec(max_prior_rows=4, gap_policy=GapPolicy.CROSS)),
            beta,
        ),
        (
            _provider(
                window=WindowSpec(
                    max_prior_rows=3,
                    gap_policy=GapPolicy.BREAK,
                    nominal_interval=1,
                )
            ),
            beta,
        ),
        (
            _provider(
                window=WindowSpec(
                    max_prior_rows=3,
                    gap_policy=GapPolicy.BREAK,
                    nominal_interval=2,
                )
            ),
            beta,
        ),
        (alpha,),
        (beta, alpha),
    )
    keys = {_identity_key(providers=value) for value in variations}
    assert base not in keys
    assert len(keys) == len(variations)


def test_parameter_encoding_is_type_and_bit_sensitive_but_order_insensitive() -> None:
    values: tuple[ParamValue, ...] = (
        1,
        1.0,
        True,
        "0x1.0000000000000p+0",
        0.0,
        -0.0,
        (1, 2),
        (2, 1),
        float("inf"),
        float("-inf"),
    )
    keys = {
        _identity_key(providers=(_provider(params={"value": value}), _second_provider()))
        for value in values
    }
    assert len(keys) == len(values)

    left = _provider(params=OrderedDict((("z", 1), ("a", 2.0))))
    right = _provider(params=OrderedDict((("a", 2.0), ("z", 1))))
    assert _identity_key(providers=(left, _second_provider())) == _identity_key(
        providers=(right, _second_provider())
    )


def test_platform_commit_dirty_state_and_diff_each_change_the_key() -> None:
    clean = PlatformIdentity(_COMMIT, False, None)
    dirty_a = PlatformIdentity(_COMMIT, True, "e" * 64)
    dirty_b = PlatformIdentity(_COMMIT, True, "f" * 64)
    other_commit = PlatformIdentity("1" * 40, False, None)
    keys = {
        _identity_key(platform_identity=value) for value in (clean, dirty_a, dirty_b, other_commit)
    }
    assert len(keys) == 4


def test_role_digest_casing_cost_hint_and_implementation_are_not_identity() -> None:
    train = DatasetRef("SYNTH_CONTEXT", "A" * 64, ViewRole.TRAIN)
    validation = DatasetRef("SYNTH_CONTEXT", "a" * 64, ViewRole.VALIDATION)
    assert train.canonical_identity == validation.canonical_identity
    left_sources = (train.canonical_identity, _sources()[1])
    right_sources = (validation.canonical_identity, _sources()[1])

    left = _provider(cost_hint=CostHint.CHEAP)
    right = _AlternateProvider(**vars(_provider(cost_hint=CostHint.EXPENSIVE)))
    assert _identity_key(
        source_identities=left_sources,
        split_policy_sha256="C" * 64,
        providers=(left, _second_provider()),
        platform_identity=PlatformIdentity("D" * 40, False, None),
    ) == _identity_key(
        source_identities=right_sources,
        providers=(right, _second_provider()),
    )


def test_construction_detaches_mutable_sequences_params_and_provider_state() -> None:
    sources = list(_sources())
    params: dict[str, ParamValue] = {"seed": 7}
    alpha = _provider(params=params)
    providers = [alpha, _second_provider()]
    identity = _identity(source_identities=sources, providers=providers)
    original_key = identity.identity_key()

    sources.reverse()
    params["seed"] = 8
    alpha.version = 99
    providers.reverse()
    assert identity.identity_key() == original_key
    assert _identity_key(source_identities=sources, providers=providers) != original_key


class _CountingProvider:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def _read[T](self, name: str, value: T) -> T:
        self.counts[name] = self.counts.get(name, 0) + 1
        return value

    @property
    def provider_id(self) -> str:
        return self._read("provider_id", "counting")

    @property
    def version(self) -> int:
        return self._read("version", 1)

    @property
    def params(self) -> Mapping[str, ParamValue]:
        return self._read("params", {"seed": 1})

    @property
    def window(self) -> WindowSpec:
        return self._read("window", WindowSpec(0, GapPolicy.CROSS))

    @property
    def feature_names(self) -> tuple[str, ...]:
        return self._read("feature_names", ("counting",))

    @property
    def required_columns(self) -> tuple[str, ...]:
        return self._read("required_columns", ())

    @property
    def cost_hint(self) -> CostHint:
        raise AssertionError("cost_hint is deliberately excluded from stage identity")

    def compute(
        self,
        request: FeatureRequest,
    ) -> Mapping[str, npt.NDArray[np.float64]]:
        raise AssertionError(f"compute is deliberately excluded: {request!r}")


def test_provider_declarations_are_read_once_and_never_revisited() -> None:
    provider = _CountingProvider()
    identity = _identity(providers=cast("Sequence[_Provider]", (provider,)))
    assert provider.counts == {
        "provider_id": 1,
        "version": 1,
        "params": 1,
        "window": 1,
        "feature_names": 1,
        "required_columns": 1,
    }
    first = identity.identity_key()
    assert identity.identity_key() == first
    assert all(count == 1 for count in provider.counts.values())


@pytest.mark.parametrize(
    ("commit", "dirty", "diff"),
    [
        ("not-a-commit", False, None),
        ("a" * 39, False, None),
        ("g" * 40, False, None),
        ("g" * 64, False, None),
        ("a" * 40, 1, None),
        ("a" * 40, True, None),
        ("a" * 40, True, "b" * 63),
        ("a" * 40, True, "g" * 64),
        ("a" * 40, False, "b" * 64),
    ],
)
def test_platform_identity_refuses_malformed_or_incomplete_git_facts(
    commit: str,
    dirty: object,
    diff: str | None,
) -> None:
    with pytest.raises(ContractViolationError):
        PlatformIdentity(commit, cast("bool", dirty), diff)


def test_platform_identity_accepts_both_commit_forms_and_normalizes_hex() -> None:
    clean = PlatformIdentity("A" * 64, False, None)
    dirty = PlatformIdentity("B" * 40, True, "C" * 64)
    assert clean.git_commit == "a" * 64
    assert dirty.git_commit == "b" * 40
    assert dirty.diff_sha256 == "c" * 64
    assert dirty == PlatformIdentity("b" * 40, True, "c" * 64)


@pytest.mark.parametrize("value", [(), "sources", (object(),)])
def test_source_identity_contract_refuses_empty_or_wrong_inputs(value: object) -> None:
    with pytest.raises(ContractViolationError):
        _identity(source_identities=cast("Sequence[DatasetIdentity]", value))


def test_dataset_ref_is_structurally_refused_even_when_its_identity_is_valid() -> None:
    reference = DatasetRef("SYNTH_CONTEXT", "a" * 64, ViewRole.TRAIN)
    with pytest.raises(ContractViolationError, match="exact DatasetIdentity"):
        _identity(source_identities=cast("Sequence[DatasetIdentity]", (reference,)))


@pytest.mark.parametrize("value", [(), "providers", (object(),)])
def test_provider_contract_refuses_empty_or_wrong_inputs(value: object) -> None:
    with pytest.raises(FeatureContractError):
        _identity(providers=cast("Sequence[_Provider]", value))


def test_bounds_require_exact_native_keys_one_axis_and_total_order() -> None:
    with pytest.raises(ContractViolationError, match="exact NativeTimeKey"):
        _identity(resolved_context_start=cast("NativeTimeKey", 10))
    with pytest.raises(TimeUnitMismatchError):
        _identity(eval_start=_key(20, unit=TimeUnit.MILLISECONDS))
    with pytest.raises(ClockDomainError):
        _identity(eval_end=_key(30, domain=ClockDomain("other.wallclock")))
    with pytest.raises(ContractViolationError, match="resolved_context_start"):
        _identity(resolved_context_start=_key(21))
    with pytest.raises(ContractViolationError, match="resolved_context_start"):
        _identity(eval_start=_key(31))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("split_policy_adr", ""),
        ("split_policy_adr", " ADR-0012"),
        ("split_policy_version", ""),
        ("split_policy_version", "v1 "),
        ("split_policy_sha256", "bad"),
        ("split_policy_sha256", "g" * 64),
    ],
)
def test_split_policy_scalars_refuse_noncanonical_values(field: str, value: str) -> None:
    with pytest.raises(ContractViolationError):
        _identity(**{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "provider",
    [
        _provider(provider_id=""),
        _provider(version=cast("int", True)),
        _provider(feature_names=()),
        _provider(feature_names=("duplicate", "duplicate")),
        _provider(required_columns=("close", "close")),
    ],
)
def test_malformed_provider_declarations_refuse(provider: _Provider) -> None:
    with pytest.raises(FeatureContractError):
        _identity(providers=(provider,))


def test_wrong_name_window_and_param_carriers_refuse() -> None:
    names = _provider()
    names.feature_names = cast("tuple[str, ...]", ["not", "a", "tuple"])
    with pytest.raises(FeatureContractError, match="exact tuple"):
        _identity(providers=(names,))

    for invalid_names in (("",), cast("tuple[str, ...]", (1,))):
        named = _provider(feature_names=invalid_names)
        with pytest.raises(FeatureContractError, match="non-empty strings"):
            _identity(providers=(named,))

    for invalid_columns in (("",), cast("tuple[str, ...]", (1,))):
        columned = _provider(required_columns=invalid_columns)
        with pytest.raises(FeatureContractError, match="non-empty strings"):
            _identity(providers=(columned,))

    window = _provider()
    window.window = cast("WindowSpec", object())
    with pytest.raises(FeatureContractError, match="exact WindowSpec"):
        _identity(providers=(window,))

    params = _provider(params=cast("Mapping[str, ParamValue]", {"bad": [1, 2]}))
    with pytest.raises(FeatureContractError, match="unsupported parameter type"):
        _identity(providers=(params,))

    non_mapping = _provider()
    non_mapping.params = cast("Mapping[str, ParamValue]", [1, 2])
    with pytest.raises(FeatureContractError, match="params must be a mapping"):
        _identity(providers=(non_mapping,))

    for invalid_params in (
        cast("Mapping[str, ParamValue]", {"": 1}),
        cast("Mapping[str, ParamValue]", {1: "value"}),
        cast("Mapping[str, ParamValue]", {"bad": {1, 2}}),
        cast("Mapping[str, ParamValue]", {"bad": {"nested": 1}}),
    ):
        with pytest.raises(FeatureContractError):
            _identity(providers=(_provider(params=invalid_params),))

    nan_params = _provider(params={"bad": float("nan")})
    with pytest.raises(FeatureContractError, match="NaN"):
        _identity(providers=(nan_params,))


def test_identity_objects_are_frozen_slotted_and_have_exact_state_fields() -> None:
    platform = PlatformIdentity(_COMMIT, False, None)
    identity = _identity(platform_identity=platform)
    assert [field.name for field in fields(platform)] == [
        "git_commit",
        "git_dirty",
        "diff_sha256",
    ]
    assert [field.name for field in fields(identity)] == [
        "_canonical_json",
        "_identity_key",
    ]
    assert not hasattr(platform, "__dict__")
    assert not hasattr(identity, "__dict__")
    with pytest.raises(FrozenInstanceError):
        platform.git_dirty = True  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        identity._identity_key = "0" * 64  # type: ignore[misc]


_CHILD_PROGRAM = """
import json
import os
import time
from dataclasses import dataclass
from trading_research.artifacts import PlatformIdentity, StageIdentity
from trading_research.core import (
    ClockDomain, CostHint, DatasetIdentity, FeatureRequest, GapPolicy,
    NativeTimeKey, TimeUnit, WindowSpec,
)

@dataclass
class Provider:
    provider_id = "child"
    version = 1
    feature_names = ("child.value",)
    required_columns = ("close",)
    window = WindowSpec(1, GapPolicy.CROSS)
    params: object = None
    cost_hint = CostHint.CHEAP
    def compute(self, request: FeatureRequest):
        raise AssertionError(request)

order = os.environ["PARAM_ORDER"]
params = {"a": 1, "z": 2.0} if order == "forward" else {"z": 2.0, "a": 1}
domain = ClockDomain("child.wallclock")
identity = StageIdentity(
    source_identities=(DatasetIdentity("CHILD", "a" * 64),),
    resolved_context_start=NativeTimeKey(10, TimeUnit.SECONDS, domain),
    eval_start=NativeTimeKey(20, TimeUnit.SECONDS, domain),
    eval_end=NativeTimeKey(30, TimeUnit.SECONDS, domain),
    split_policy_adr="ADR-0012",
    split_policy_version="v1",
    split_policy_sha256="b" * 64,
    providers=(Provider(params=params),),
    platform_identity=PlatformIdentity("c" * 40, False, None),
)
print(json.dumps({
    "key": identity.identity_key(),
    "pid": os.getpid(),
    "cwd": os.getcwd(),
    "hostname": os.environ["HOSTNAME"],
    "observed_time": time.time_ns(),
}, sort_keys=True))
"""


def _run_child(*, cwd: Path, hash_seed: str, host: str, order: str) -> dict[str, object]:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONHASHSEED": hash_seed,
            "HOSTNAME": host,
            "PARAM_ORDER": order,
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_PROGRAM],
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return cast("dict[str, object]", json.loads(completed.stdout))


def test_key_is_stable_across_process_hashseed_cwd_host_pid_time_and_param_order(
    tmp_path: Path,
) -> None:
    left_dir = tmp_path / "left"
    right_dir = tmp_path / "right"
    left_dir.mkdir()
    right_dir.mkdir()
    left = _run_child(cwd=left_dir, hash_seed="1", host="first-host", order="forward")
    right = _run_child(cwd=right_dir, hash_seed="987654", host="second-host", order="reverse")
    assert left["key"] == right["key"]
    assert left["pid"] != right["pid"]
    assert left["cwd"] != right["cwd"]
    assert left["hostname"] != right["hostname"]
    assert left["observed_time"] != right["observed_time"]
