"""Complete fitted-state identity, serialization, and materialization tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from trading_research.artifacts.fitted import (
    _bind_fitted_state,
    _decode_fitted_state,
    _encode_fitted_state,
    _FittedStateIdentity,
    _materialize_fitted_state,
)
from trading_research.artifacts.identity import PlatformIdentity
from trading_research.artifacts.materialization import MaterializationPolicy
from trading_research.artifacts.store import ArtifactStore
from trading_research.core import (
    ClockDomain,
    ContractViolationError,
    CostHint,
    DatasetIdentity,
    FeatureOrigin,
    FittedPayload,
    GapPolicy,
    NativeTimeKey,
    StageOrigin,
    TimeUnit,
    ToleranceSpec,
    ViewSpan,
    WindowSpec,
)
from trading_research.core.research_frames import _make_training_target_frame

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from trading_research.core import ParamValue, TrainingTargetFrame

DOMAIN = ClockDomain("test.native")
SOURCE = DatasetIdentity("TRAIN_TEST", "1" * 64)
FEATURE = FeatureOrigin("feature", 1, {}, WindowSpec(1, GapPolicy.CROSS))
REPRESENTATION = StageOrigin("representation", 1, {})
PLATFORM = PlatformIdentity("a" * 40, False, None)


def _key(value: int) -> NativeTimeKey:
    return NativeTimeKey(value, TimeUnit.SECONDS, DOMAIN)


def _view(*, end: int = 13) -> ViewSpan:
    return ViewSpan((SOURCE,), TimeUnit.SECONDS, DOMAIN, _key(10), _key(10), _key(end))


def _targets(
    *,
    horizon: int = 1,
    keys: tuple[int, ...] = (10, 11, 12, 13),
    values: tuple[float, ...] | None = None,
) -> TrainingTargetFrame:
    origin = StageOrigin(
        "forward_log_return_v1",
        1,
        {
            "boundary_policy": "nan",
            "decision_delay_rows": 1,
            "horizon_rows": horizon,
            "reference_price_column": "close",
        },
    )
    return _make_training_target_frame(
        view=_view(),
        keys=np.asarray(keys, dtype=np.int64),
        columns={
            "target.forward_log_return": np.asarray(
                values
                if values is not None
                else np.linspace(0.1, 0.1 * len(keys), len(keys)),
                dtype=np.float64,
            )
        },
        origins={"target.forward_log_return": origin},
    )


def _identity(
    *,
    plugin_id: str = "model",
    plugin_params: Mapping[str, ParamValue] = {"seed": 7},
    feature_origin: FeatureOrigin = FEATURE,
    representation_key: str = "2" * 64,
    view: ViewSpan | None = None,
    split_sha256: str = "3" * 64,
    targets: TrainingTargetFrame | None = None,
    fit_isolation: Mapping[str, ParamValue] = {
        "applied_purge_rows": 0,
        "embargo_rows": 0,
        "derivation": ("slice_3c_no_fold",),
    },
    platform: PlatformIdentity = PLATFORM,
    dependency: str = "4" * 64,
    tolerance: ToleranceSpec | None = None,
) -> _FittedStateIdentity:
    return _FittedStateIdentity(
        plugin_kind="model",
        plugin_id=plugin_id,
        plugin_version=1,
        plugin_params=plugin_params,
        feature_origins=(("log_return_1", feature_origin),),
        representation_origins=(("standardized_log_return_1", REPRESENTATION),),
        representation_state_identity_key=representation_key,
        training_view=_view() if view is None else view,
        split_policy_adr="ADR-0012",
        split_policy_version="v1",
        split_policy_sha256=split_sha256,
        training_targets=_targets() if targets is None else targets,
        fit_isolation=fit_isolation,
        platform_identity=platform,
        dependency_lock_sha256=dependency,
        comparison_tolerance=tolerance,
    )


def test_complete_identity_is_deterministic_and_sensitive_to_all_eleven_components() -> None:
    base = _identity().identity_key()
    variants = {
        "plugin": _identity(plugin_id="other-model").identity_key(),
        "features": _identity(
            feature_origin=FeatureOrigin(
                "feature", 2, {}, WindowSpec(1, GapPolicy.CROSS)
            )
        ).identity_key(),
        "representation": _identity(representation_key="5" * 64).identity_key(),
        "training_view": _identity(view=_view(end=14)).identity_key(),
        "split_policy": _identity(split_sha256="6" * 64).identity_key(),
        "outcome": _identity(targets=_targets(horizon=2)).identity_key(),
        "fit_isolation": _identity(
            fit_isolation={
                "applied_purge_rows": 1,
                "embargo_rows": 0,
                "derivation": ("test",),
            }
        ).identity_key(),
        "platform": _identity(
            platform=PlatformIdentity("b" * 40, False, None)
        ).identity_key(),
        "dependencies": _identity(dependency="7" * 64).identity_key(),
        "seed": _identity(plugin_params={"seed": 8}).identity_key(),
        "tolerance": _identity(
            tolerance=ToleranceSpec(0.0, 1e-12, "declared deterministic test")
        ).identity_key(),
    }

    assert _identity().identity_key() == base
    assert len({base, *variants.values()}) == 12
    assert all(len(key) == 64 and key == key.lower() for key in variants.values())


def test_same_target_values_under_a_different_definition_never_share_identity() -> None:
    first = _targets(horizon=1)
    second = _targets(horizon=2)

    np.testing.assert_array_equal(
        first.column("target.forward_log_return"),
        second.column("target.forward_log_return"),
    )
    assert _identity(targets=first).identity_key() != _identity(targets=second).identity_key()


def test_identity_excludes_payload_values_target_values_and_mapping_order() -> None:
    first_targets = _targets(values=(0.1, 0.2, 0.3, 0.4))
    second_targets = _targets(values=(-9.0, 8.0, -7.0, 6.0))
    first_identity = _identity(
        plugin_params={"alpha": 1, "seed": 7},
        targets=first_targets,
    )
    second_identity = _identity(
        plugin_params={"seed": 7, "alpha": 1},
        targets=second_targets,
    )

    assert first_identity.identity_key() == second_identity.identity_key()
    first = _bind_fitted_state(
        first_identity,
        FittedPayload(
            arrays={"learned": np.array([1.0], dtype=np.float64)},
            metadata={"state_schema": "first"},
        ),
    )
    second = _bind_fitted_state(
        second_identity,
        FittedPayload(
            arrays={"learned": np.array([999.0], dtype=np.float64)},
            metadata={"state_schema": "second"},
        ),
    )
    assert first.identity_key() == second.identity_key()


def test_pickle_free_round_trip_preserves_shapes_dtypes_metadata_and_identity() -> None:
    identity = _identity()
    payload = FittedPayload(
        arrays={
            "matrix": np.array([[1.0, np.nan], [3.0, 4.0]], dtype=np.float64),
            "counts": np.array([1, 2, 3], dtype=np.int64),
            "mask": np.array([True, False], dtype=np.bool_),
            "empty": np.array([], dtype=np.float32),
        },
        metadata={"schema": "test_v1", "nested": (1, -0.0, None)},
    )
    state = _bind_fitted_state(identity, payload)

    encoded = _encode_fitted_state(identity, state)
    restored = _decode_fitted_state(encoded, expected_identity_key=identity.identity_key())

    assert encoded == _encode_fitted_state(identity, state)
    assert restored.identity_key() == state.identity_key()
    assert restored.payload.array_names == ("counts", "empty", "mask", "matrix")
    for name in restored.payload.array_names:
        assert np.array_equal(
            restored.payload.array(name), payload.array(name), equal_nan=True
        )
        assert restored.payload.array(name).dtype == payload.array(name).dtype
        assert restored.payload.array(name).shape == payload.array(name).shape
        assert not restored.payload.array(name).flags.writeable
    nested = restored.payload.metadata["nested"]
    assert type(nested) is tuple
    signed_zero = nested[1]
    assert type(signed_zero) is float
    assert signed_zero.hex() == "-0x0.0p+0"
    with pytest.raises(ContractViolationError, match="does not match"):
        _decode_fitted_state(encoded, expected_identity_key="f" * 64)


def test_cache_if_expensive_reuses_the_store_and_cheap_state_stays_ephemeral(
    tmp_path: Path,
) -> None:
    identity = _identity()
    store = ArtifactStore(tmp_path)
    calls = 0
    validations = 0

    def fit() -> FittedPayload:
        nonlocal calls
        calls += 1
        return FittedPayload(
            arrays={"weight": np.array([2.0], dtype=np.float64)},
            metadata={"schema": "cache_test_v1"},
        )

    def validate() -> None:
        nonlocal validations
        validations += 1

    first = _materialize_fitted_state(
        identity=identity,
        cost_hint=CostHint.EXPENSIVE,
        store=store,
        policy=MaterializationPolicy.CACHE_IF_EXPENSIVE,
        validate_after_lookup=validate,
        fit=fit,
    )
    second = _materialize_fitted_state(
        identity=identity,
        cost_hint=CostHint.EXPENSIVE,
        store=store,
        policy=MaterializationPolicy.CACHE_IF_EXPENSIVE,
        validate_after_lookup=validate,
        fit=fit,
    )

    assert calls == validations == 1
    assert first.identity_key() == second.identity_key()
    assert second.payload.array("weight").tolist() == [2.0]
    identity_path = tmp_path / "cache" / "fitted-states" / identity.identity_key()
    assert len(tuple(identity_path.glob("*.npz"))) == 1

    cheap_identity = _identity(plugin_id="cheap-model")
    _materialize_fitted_state(
        identity=cheap_identity,
        cost_hint=CostHint.CHEAP,
        store=store,
        policy=MaterializationPolicy.CACHE_IF_EXPENSIVE,
        validate_after_lookup=validate,
        fit=fit,
    )
    assert not (tmp_path / "cache" / "fitted-states" / cheap_identity.identity_key()).exists()
