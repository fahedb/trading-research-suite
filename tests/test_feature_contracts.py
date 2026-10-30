"""Slice 2A columnar feature contracts and immutable carriers."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import FrozenInstanceError
from enum import IntEnum
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pytest

from trading_research.core import (
    ClockDomain,
    CostHint,
    DatasetIdentity,
    DatasetRef,
    FeatureContractError,
    FeatureFrame,
    FeatureFrameError,
    FeatureOrigin,
    FeatureRequest,
    FeatureRequestError,
    GapPolicy,
    NativeTimeKey,
    ParamValue,
    TimeUnit,
    ViewRole,
    WindowSpec,
    canonical_params_json,
)
from trading_research.core.feature_frames import _make_feature_frame
from trading_research.core.frames import MarketFrame, _make_market_frame

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import numpy.typing as npt

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DOMAIN = ClockDomain("venue.wallclock")
OTHER_DOMAIN = ClockDomain("other.wallclock")


def _key(
    value: int,
    *,
    unit: TimeUnit = TimeUnit.SECONDS,
    domain: ClockDomain = DOMAIN,
) -> NativeTimeKey:
    return NativeTimeKey(value=value, unit=unit, clock_domain=domain)


def _frame(
    keys: Sequence[int],
    *,
    dataset_id: str = "TRAIN_V1",
    digest: str = DIGEST_A,
    role: ViewRole = ViewRole.TRAIN,
    unit: TimeUnit = TimeUnit.SECONDS,
    domain: ClockDomain = DOMAIN,
    close_dtype: npt.DTypeLike = np.float64,
    admitted_lo: int | None = None,
    admitted_hi: int | None = None,
) -> MarketFrame:
    key_array = np.asarray(keys, dtype=np.int64)
    lower = admitted_lo if admitted_lo is not None else (int(key_array[0]) if key_array.size else 0)
    upper = (
        admitted_hi if admitted_hi is not None else (int(key_array[-1]) if key_array.size else 9)
    )
    close = np.arange(key_array.size, dtype=close_dtype) + 100
    volume = np.arange(key_array.size, dtype=np.int64) + 1
    return _make_market_frame(
        dataset_ref=DatasetRef(dataset_id, digest, role),
        clock_domain=domain,
        unit=unit,
        schema={"close": str(close.dtype), "volume": "int64"},
        keys=key_array,
        columns={"close": close, "volume": volume},
        admitted_lo=lower,
        admitted_hi=upper,
    )


def _request(
    *,
    frames: Sequence[MarketFrame] | None = None,
    eval_start: int = 30,
    eval_end: int = 50,
    required_columns: Sequence[str] = ("close", "volume"),
) -> FeatureRequest:
    source = (
        (
            _frame((10, 20)),
            _frame(
                (30, 40, 50),
                dataset_id="VALIDATION_V1",
                digest=DIGEST_B,
                role=ViewRole.VALIDATION,
            ),
        )
        if frames is None
        else frames
    )
    return FeatureRequest.from_segments(
        source,
        eval_start=_key(eval_start),
        eval_end=_key(eval_end),
        required_columns=required_columns,
    )


def _origin(*, provider_id: str = "provider", version: int = 1) -> FeatureOrigin:
    return FeatureOrigin(
        provider_id=provider_id,
        version=version,
        params={"window": 2, "scale": 1.5},
        window=WindowSpec(max_prior_rows=1, gap_policy=GapPolicy.CROSS),
    )


def _feature_frame(
    *,
    request: FeatureRequest | None = None,
    columns: Mapping[str, npt.NDArray[Any]] | None = None,
    origins: Mapping[str, FeatureOrigin] | None = None,
) -> FeatureFrame:
    bounded = _request() if request is None else request
    values = (
        OrderedDict(
            (
                ("return_1", np.array([np.nan, 0.1, 0.2], dtype=np.float64)),
                ("range", np.array([1.0, 2.0, 3.0], dtype=np.float64)),
            )
        )
        if columns is None
        else columns
    )
    provenance = (
        OrderedDict((name, _origin(provider_id=name or "provider")) for name in values)
        if origins is None
        else origins
    )
    return _make_feature_frame(
        request=bounded,
        columns=values,
        origins=provenance,
    )


class TestEnumsAndWindows:
    def test_vocabularies_are_deliberately_closed(self) -> None:
        assert tuple(GapPolicy) == (GapPolicy.CROSS, GapPolicy.BREAK)
        assert tuple(CostHint) == (CostHint.CHEAP, CostHint.EXPENSIVE)

    @pytest.mark.parametrize("reach", [False, -1, 1.5, "1"])
    def test_invalid_reach_refuses(self, reach: object) -> None:
        with pytest.raises(FeatureContractError, match="max_prior_rows"):
            WindowSpec(
                max_prior_rows=cast("int", reach),
                gap_policy=GapPolicy.CROSS,
            )

    def test_reach_and_interval_require_exact_python_ints(self) -> None:
        class IntegerSubclass(int):
            pass

        with pytest.raises(FeatureContractError, match="max_prior_rows"):
            WindowSpec(cast("int", IntegerSubclass(1)), GapPolicy.CROSS)
        with pytest.raises(FeatureContractError, match="nominal_interval"):
            WindowSpec(1, GapPolicy.BREAK, nominal_interval=cast("int", IntegerSubclass(60)))

    def test_cross_has_no_interval_and_zero_reach_is_legal(self) -> None:
        assert WindowSpec(0, GapPolicy.CROSS) == WindowSpec(
            max_prior_rows=0,
            gap_policy=GapPolicy.CROSS,
            nominal_interval=None,
        )
        with pytest.raises(FeatureContractError, match="must be None"):
            WindowSpec(0, GapPolicy.CROSS, nominal_interval=60)

    @pytest.mark.parametrize("interval", [None, False, 0, -1, 1.5, 2**63])
    def test_break_requires_a_positive_int64_interval(self, interval: object) -> None:
        with pytest.raises(FeatureContractError, match=r"nominal_interval|BREAK"):
            WindowSpec(
                max_prior_rows=1,
                gap_policy=GapPolicy.BREAK,
                nominal_interval=cast("int | None", interval),
            )

    def test_break_accepts_an_explicit_interval_and_specs_are_frozen(self) -> None:
        spec = WindowSpec(19, GapPolicy.BREAK, nominal_interval=60)
        assert spec.max_prior_rows == 19
        with pytest.raises(FrozenInstanceError):
            spec.max_prior_rows = 20  # type: ignore[misc]


class TestCanonicalParametersAndOrigins:
    def test_canonical_encoding_ignores_mapping_order_and_preserves_types(self) -> None:
        left: OrderedDict[str, ParamValue] = OrderedDict(
            (
                ("z", (1, 1.0, "0x1.0000000000000p+0", -0.0)),
                ("a", True),
            )
        )
        right: OrderedDict[str, ParamValue] = OrderedDict(reversed(tuple(left.items())))

        encoded = canonical_params_json(left)

        assert encoded == canonical_params_json(right)
        assert '"__float_hex__":"0x1.0000000000000p+0"' in encoded
        assert '"__float_hex__":"-0x0.0p+0"' in encoded
        assert '"0x1.0000000000000p+0"' in encoded
        assert canonical_params_json({"x": 1}) != canonical_params_json({"x": 1.0})

    def test_canonical_encoding_is_ascii_and_supports_signed_infinity(self) -> None:
        encoded = canonical_params_json({"label": "תכונה", "values": (float("inf"), float("-inf"))})
        assert encoded.isascii()
        assert '"inf"' in encoded
        assert '"-inf"' in encoded

    @pytest.mark.parametrize("value", [float("nan"), -float("nan")])
    def test_nan_parameters_refuse_noninjective_identity(self, value: float) -> None:
        with pytest.raises(FeatureContractError, match="NaN"):
            canonical_params_json({"value": value})

    @pytest.mark.parametrize(
        "value",
        [[], {}, {1}, b"bytes", np.int64(1), object()],
        ids=["list", "mapping", "set", "bytes", "numpy-scalar", "object"],
    )
    def test_noncanonical_parameter_values_refuse(self, value: object) -> None:
        params = cast("Mapping[str, ParamValue]", {"bad": value})
        with pytest.raises(FeatureContractError, match="unsupported parameter type"):
            canonical_params_json(params)

    @pytest.mark.parametrize("name", ["", 1])
    def test_parameter_names_must_be_nonempty_strings(self, name: object) -> None:
        params = cast("Mapping[str, ParamValue]", {name: 1})
        with pytest.raises(FeatureContractError, match="parameter name"):
            canonical_params_json(params)

    def test_origin_copies_and_freezes_parameters(self) -> None:
        params: dict[str, ParamValue] = {"b": 2, "a": (1, 1.5)}
        origin = FeatureOrigin(
            provider_id="provider",
            version=0,
            params=params,
            window=WindowSpec(0, GapPolicy.CROSS),
        )
        params["a"] = 99

        assert dict(origin.params) == {"a": (1, 1.5), "b": 2}
        with pytest.raises(TypeError):
            origin.params["new"] = 1  # type: ignore[index]

    @pytest.mark.parametrize(
        ("provider_id", "version"),
        [("", 1), (cast("str", 1), 1), ("provider", True), ("provider", 1.0)],
    )
    def test_invalid_origin_identity_refuses(self, provider_id: str, version: object) -> None:
        with pytest.raises(FeatureContractError):
            FeatureOrigin(
                provider_id=provider_id,
                version=cast("int", version),
                params={},
                window=WindowSpec(0, GapPolicy.CROSS),
            )

    def test_identity_bearing_integers_refuse_intenum(self) -> None:
        class Version(IntEnum):
            ONE = 1

        with pytest.raises(FeatureContractError, match="version"):
            FeatureOrigin(
                provider_id="provider",
                version=cast("int", Version.ONE),
                params={},
                window=WindowSpec(0, GapPolicy.CROSS),
            )


class TestFeatureRequest:
    def test_direct_construction_refuses(self) -> None:
        with pytest.raises(TypeError, match="from_segments"):
            FeatureRequest()

    def test_concatenates_only_declared_columns_and_binds_final_eval_suffix(self) -> None:
        request = _request(required_columns=("volume", "close"))

        assert request.keys.tolist() == [10, 20, 30, 40, 50]
        assert request.eval_offset == 2
        assert request.eval_length == 3
        assert request.column("volume").dtype == np.dtype(np.int64)
        assert request.column("close").dtype == np.dtype(np.float64)
        assert request.source_identities == (
            DatasetIdentity("TRAIN_V1", DIGEST_A),
            DatasetIdentity("VALIDATION_V1", DIGEST_B),
        )
        with pytest.raises(FeatureRequestError, match="not declared"):
            request.column("open")

    def test_empty_context_segment_is_legal_and_preserves_identity(self) -> None:
        empty = _frame((), admitted_lo=10, admitted_hi=20)
        final = _frame(
            (30, 40),
            dataset_id="VALIDATION_V1",
            digest=DIGEST_B,
            role=ViewRole.VALIDATION,
        )
        request = _request(frames=(empty, final), eval_start=30, eval_end=40)

        assert request.keys.tolist() == [30, 40]
        assert request.eval_offset == 0
        assert request.eval_length == 2
        assert request.source_identities[0] == empty.dataset_ref.canonical_identity

    def test_empty_required_columns_is_legal(self) -> None:
        request = _request(required_columns=())
        assert request.keys.tolist() == [10, 20, 30, 40, 50]
        with pytest.raises(FeatureRequestError, match="not declared"):
            request.column("close")

    def test_requested_bound_inside_a_gap_binds_observed_axis(self) -> None:
        final = _frame(
            (30, 50),
            dataset_id="VALIDATION_V1",
            digest=DIGEST_B,
            role=ViewRole.VALIDATION,
        )
        request = _request(
            frames=(_frame((10, 20)), final),
            eval_start=40,
            eval_end=50,
        )
        assert request.keys[request.eval_offset :].tolist() == [50]
        assert request.eval_length == 1

    @pytest.mark.parametrize(
        "frames",
        [
            (),
            cast("tuple[MarketFrame, ...]", (object(),)),
        ],
        ids=["empty", "fabricated"],
    )
    def test_malformed_frame_sequences_refuse(self, frames: tuple[MarketFrame, ...]) -> None:
        with pytest.raises(FeatureRequestError):
            _request(frames=frames)

    @pytest.mark.parametrize(
        "required_columns",
        [cast("Sequence[str]", "close"), ("close", "close"), ("",), cast("Sequence[str]", (1,))],
        ids=["bare-string", "duplicate", "empty-name", "non-string"],
    )
    def test_malformed_required_columns_refuse(self, required_columns: Sequence[str]) -> None:
        with pytest.raises(
            FeatureRequestError,
            match=r"required_columns|required column",
        ):
            _request(required_columns=required_columns)

    def test_missing_required_column_is_wrapped_as_request_error(self) -> None:
        with pytest.raises(FeatureRequestError, match=r"does not provide.*missing"):
            _request(required_columns=("missing",))

    def test_mixed_axis_refuses(self) -> None:
        with pytest.raises(FeatureRequestError, match="milliseconds"):
            _request(
                frames=(
                    _frame((10, 20)),
                    _frame(
                        (30, 40, 50),
                        dataset_id="VALIDATION_V1",
                        digest=DIGEST_B,
                        role=ViewRole.VALIDATION,
                        unit=TimeUnit.MILLISECONDS,
                    ),
                )
            )
        with pytest.raises(FeatureRequestError, match=r"other\.wallclock"):
            _request(
                frames=(
                    _frame((10, 20)),
                    _frame(
                        (30, 40, 50),
                        dataset_id="VALIDATION_V1",
                        digest=DIGEST_B,
                        role=ViewRole.VALIDATION,
                        domain=OTHER_DOMAIN,
                    ),
                )
            )

    def test_mixed_required_column_dtype_refuses(self) -> None:
        with pytest.raises(FeatureRequestError, match="changes dtype"):
            _request(
                frames=(
                    _frame((10, 20), close_dtype=np.float32),
                    _frame(
                        (30, 40, 50),
                        dataset_id="VALIDATION_V1",
                        digest=DIGEST_B,
                        role=ViewRole.VALIDATION,
                        close_dtype=np.float64,
                    ),
                ),
                required_columns=("close",),
            )

    @pytest.mark.parametrize("target", ["keys", "column"])
    def test_dtype_metadata_refuses_mutable_descriptor_state(self, target: str) -> None:
        key_dtype = np.dtype(np.int64, metadata={"mutable": []})
        value_dtype = np.dtype(np.float64, metadata={"mutable": []})
        keys = np.array([30, 40, 50], dtype=key_dtype if target == "keys" else np.int64)
        close = np.arange(3, dtype=value_dtype if target == "column" else np.float64)
        frame = _make_market_frame(
            dataset_ref=DatasetRef("VALIDATION_V1", DIGEST_B, ViewRole.VALIDATION),
            clock_domain=DOMAIN,
            unit=TimeUnit.SECONDS,
            schema={"close": "float64"},
            keys=keys,
            columns={"close": close},
            admitted_lo=30,
            admitted_hi=50,
        )

        with pytest.raises(FeatureRequestError, match="metadata-free"):
            _request(
                frames=(frame,),
                eval_start=30,
                eval_end=50,
                required_columns=("close",),
            )

    @pytest.mark.parametrize(
        "context_keys",
        [(10, 30), (10, 40), (10, 50)],
        ids=["reaches-eval", "overlaps", "duplicate-seam"],
    )
    def test_context_may_not_own_evaluation_rows(self, context_keys: tuple[int, ...]) -> None:
        with pytest.raises(FeatureRequestError):
            _request(
                frames=(
                    _frame(context_keys),
                    _frame(
                        (30, 40, 50),
                        dataset_id="VALIDATION_V1",
                        digest=DIGEST_B,
                        role=ViewRole.VALIDATION,
                    ),
                )
            )

    def test_cross_segment_reverse_order_refuses(self) -> None:
        with pytest.raises(FeatureRequestError, match="unordered"):
            _request(
                frames=(
                    _frame((10, 20)),
                    _frame(
                        (15, 30, 40),
                        dataset_id="VALIDATION_V1",
                        digest=DIGEST_B,
                        role=ViewRole.VALIDATION,
                        admitted_lo=15,
                        admitted_hi=40,
                    ),
                ),
                eval_start=30,
                eval_end=40,
            )

    @pytest.mark.parametrize(
        ("first_context", "second_context", "message"),
        [
            ((5, 10), (10, 20), "duplicate"),
            ((5, 15), (10, 20), "overlap or are unordered"),
        ],
    )
    def test_context_seams_explicitly_refuse_duplicate_and_overlap(
        self,
        first_context: tuple[int, ...],
        second_context: tuple[int, ...],
        message: str,
    ) -> None:
        final = _frame(
            (30, 40),
            dataset_id="VALIDATION_V1",
            digest=DIGEST_B,
            role=ViewRole.VALIDATION,
        )
        with pytest.raises(FeatureRequestError, match=message):
            _request(
                frames=(
                    _frame(first_context, dataset_id="CONTEXT_A"),
                    _frame(second_context, dataset_id="CONTEXT_B", digest="c" * 64),
                    final,
                ),
                eval_start=30,
                eval_end=40,
            )

    def test_any_key_past_eval_end_refuses_instead_of_trimming(self) -> None:
        with pytest.raises(FeatureRequestError, match="past eval_end"):
            _request(eval_end=40)

    def test_inverted_or_empty_observed_evaluation_refuses(self) -> None:
        with pytest.raises(FeatureRequestError, match="inverted"):
            _request(eval_start=50, eval_end=40)
        with pytest.raises(FeatureRequestError, match="contains no observed rows"):
            _request(
                frames=(
                    _frame((10,)),
                    _frame(
                        (20, 25),
                        dataset_id="VALIDATION_V1",
                        digest=DIGEST_B,
                        role=ViewRole.VALIDATION,
                    ),
                ),
                eval_start=26,
                eval_end=29,
            )

    def test_public_arrays_are_immutable_fresh_descriptors(self) -> None:
        request = _request()
        for accessor in (lambda: request.keys, lambda: request.column("close")):
            first = accessor()
            expected = first.copy()
            with pytest.raises(ValueError, match="WRITEABLE flag"):
                first.setflags(write=True)
            with pytest.warns(DeprecationWarning, match="Setting the dtype"):
                first.dtype = np.uint8  # type: ignore[misc, assignment]
            fresh = accessor()
            assert fresh.ndim == 1
            assert fresh.dtype == expected.dtype
            np.testing.assert_array_equal(fresh, expected)
            assert fresh is not first


class TestFeatureFrame:
    def test_direct_construction_refuses_and_surface_is_exact(self) -> None:
        with pytest.raises(TypeError, match="feature platform"):
            FeatureFrame()
        assert {name for name in FeatureFrame.__dict__ if not name.startswith("_")} == {
            "clock_domain",
            "column",
            "column_names",
            "keys",
            "num_rows",
            "origin",
            "schema",
            "source_identities",
            "unit",
        }

    def test_frame_is_dense_ordered_and_bound_to_request_evaluation_keys(self) -> None:
        request = _request()
        frame = _feature_frame(request=request)

        np.testing.assert_array_equal(
            frame.keys,
            request.keys[request.eval_offset : request.eval_offset + request.eval_length],
        )
        assert frame.column_names == ("return_1", "range")
        assert dict(frame.schema) == {"return_1": "float64", "range": "float64"}
        assert frame.num_rows == 3
        assert frame.unit is TimeUnit.SECONDS
        assert frame.clock_domain == DOMAIN
        assert frame.source_identities == request.source_identities
        assert np.isnan(frame.column("return_1")[0])
        assert frame.origin("range").provider_id == "range"

    def test_schema_is_read_only_and_arrays_are_defensively_detached(self) -> None:
        values = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        frame = _feature_frame(columns={"value": values})
        values[:] = 99.0

        np.testing.assert_array_equal(frame.column("value"), [1.0, 2.0, 3.0])
        with pytest.raises(TypeError):
            frame.schema["other"] = "float64"  # type: ignore[index]
        for accessor in (lambda: frame.keys, lambda: frame.column("value")):
            first = accessor()
            expected = first.copy()
            with pytest.raises(ValueError, match="WRITEABLE flag"):
                first.setflags(write=True)
            with pytest.warns(DeprecationWarning, match="Setting the dtype"):
                first.dtype = np.uint8  # type: ignore[misc, assignment]
            fresh = accessor()
            assert fresh.ndim == 1
            assert fresh.dtype == expected.dtype
            np.testing.assert_array_equal(fresh, expected)

    @pytest.mark.parametrize(
        "columns",
        [
            {},
            {"": np.ones(3, dtype=np.float64)},
            {"value": [1.0, 2.0, 3.0]},
            {"value": np.ones((3, 1), dtype=np.float64)},
            {"value": np.ones(3, dtype=np.float32)},
            {"value": np.ones(2, dtype=np.float64)},
            {"value": np.array([1.0, np.inf, 3.0], dtype=np.float64)},
            {"value": np.array([1.0, -np.inf, 3.0], dtype=np.float64)},
        ],
        ids=[
            "empty",
            "empty-name",
            "not-array",
            "not-vector",
            "wrong-dtype",
            "wrong-length",
            "positive-infinity",
            "negative-infinity",
        ],
    )
    def test_invalid_feature_columns_refuse(self, columns: Mapping[str, object]) -> None:
        typed = cast("Mapping[str, npt.NDArray[Any]]", columns)
        with pytest.raises(FeatureFrameError):
            _feature_frame(columns=typed)

    def test_ndarray_subclass_refuses(self) -> None:
        class ArraySubclass(np.ndarray):
            pass

        values = np.ones(3, dtype=np.float64).view(ArraySubclass)
        with pytest.raises(FeatureFrameError, match="NumPy array"):
            _feature_frame(columns={"value": values})

    def test_dtype_metadata_refuses_mutable_descriptor_state(self) -> None:
        metadata: dict[str, list[object]] = {"mutable": []}
        values = np.ones(3, dtype=np.dtype(np.float64, metadata=metadata))
        with pytest.raises(FeatureFrameError, match="metadata-free"):
            _feature_frame(columns={"value": values})

    def test_origin_names_must_match_exactly_without_order_coupling(self) -> None:
        columns = OrderedDict(
            (
                ("a", np.ones(3, dtype=np.float64)),
                ("b", np.ones(3, dtype=np.float64)),
            )
        )
        for origins in (
            {"a": _origin()},
            {"a": _origin(), "b": _origin(), "extra": _origin()},
        ):
            with pytest.raises(FeatureFrameError, match="exactly match"):
                _feature_frame(columns=columns, origins=origins)

        reversed_origins = OrderedDict((("b", _origin()), ("a", _origin())))
        assert _feature_frame(columns=columns, origins=reversed_origins).column_names == (
            "a",
            "b",
        )

    def test_wrong_origin_type_refuses(self) -> None:
        with pytest.raises(FeatureFrameError, match="FeatureOrigin"):
            _feature_frame(
                columns={"value": np.ones(3, dtype=np.float64)},
                origins=cast("Mapping[str, FeatureOrigin]", {"value": object()}),
            )

    @pytest.mark.parametrize("name", ["missing", cast("str", 1)])
    def test_unknown_or_nonstring_lookup_refuses(self, name: str) -> None:
        frame = _feature_frame()
        with pytest.raises(FeatureFrameError):
            frame.column(name)
        with pytest.raises(FeatureFrameError):
            frame.origin(name)

    def test_no_dataframe_or_row_conveniences_exist(self) -> None:
        frame = _feature_frame()
        for name in ("__iter__", "__getitem__", "select", "slice_by_keys", "to_pandas"):
            assert not hasattr(frame, name)
