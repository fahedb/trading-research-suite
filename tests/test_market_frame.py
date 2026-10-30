from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pytest

from trading_research.core import (
    ClockDomain,
    ClockDomainError,
    ContractViolationError,
    DatasetRef,
    IntegrityError,
    MarketFrame,
    MarketFrameError,
    NativeTimeKey,
    PartitionReadError,
    TimeUnit,
    TimeUnitMismatchError,
    TradingResearchError,
    UnsupportedMarketFrameRoleError,
    ViewRole,
)
from trading_research.core.frames import _make_market_frame

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import numpy.typing as npt

DIGEST = "a" * 64
DOMAIN = ClockDomain("venue.wallclock")
OTHER_DOMAIN = ClockDomain("other.wallclock")
DATASET_REF = DatasetRef("TRAIN_V1", DIGEST, ViewRole.TRAIN)


def _frame(
    *,
    keys: npt.NDArray[Any] | None = None,
    schema: Mapping[str, str] | None = None,
    columns: Mapping[str, npt.NDArray[Any]] | None = None,
    admitted_lo: int = 10,
    admitted_hi: int = 40,
) -> MarketFrame:
    frame_keys = np.array([10, 20, 30, 40], dtype=np.int64) if keys is None else keys
    frame_schema = {"price": "float64", "volume": "uint64"} if schema is None else schema
    frame_columns = (
        {
            "price": np.array([1.0, 1.5, 2.0, 2.5], dtype=np.float64),
            "volume": np.array([5, 6, 7, 8], dtype=np.uint64),
        }
        if columns is None
        else columns
    )
    return _make_market_frame(
        dataset_ref=DATASET_REF,
        clock_domain=DOMAIN,
        unit=TimeUnit.SECONDS,
        schema=frame_schema,
        keys=frame_keys,
        columns=frame_columns,
        admitted_lo=admitted_lo,
        admitted_hi=admitted_hi,
    )


def _key(
    value: int,
    *,
    unit: TimeUnit = TimeUnit.SECONDS,
    domain: ClockDomain = DOMAIN,
) -> NativeTimeKey:
    return NativeTimeKey(value=value, unit=unit, clock_domain=domain)


def _ordinary_ndarray_base_chain(
    array: npt.NDArray[Any],
) -> tuple[npt.NDArray[Any], ...]:
    """Return every ndarray reachable through ordinary ``.base`` traversal."""
    chain: list[npt.NDArray[Any]] = []
    current: object = array
    while isinstance(current, np.ndarray):
        chain.append(current)
        current = current.base
    return tuple(chain)


def _immutable_buffer(array: npt.NDArray[Any]) -> bytes:
    """Return the terminal immutable bytes backing one public descriptor."""
    base: object = array.base
    assert isinstance(base, bytes)
    return base


def _assert_no_writable_ndarray_owner(
    array: npt.NDArray[Any],
    *,
    sibling: MarketFrame,
) -> None:
    """Prove public/base arrays cannot enable mutation visible to a sibling."""
    sibling_keys = sibling.keys.copy()
    sibling_columns = {
        name: sibling.column(name).copy() for name in sibling.column_names
    }
    for candidate in _ordinary_ndarray_base_chain(array):
        with pytest.raises(ValueError, match="cannot set WRITEABLE flag"):
            candidate.setflags(write=True)
        assert not candidate.flags.writeable
        if candidate.size:
            with pytest.raises(ValueError, match="read-only"):
                candidate.flat[0] = 0
        np.testing.assert_array_equal(sibling.keys, sibling_keys)
        for name, expected in sibling_columns.items():
            np.testing.assert_array_equal(sibling.column(name), expected)


def _public_array_accessors(
    frame: MarketFrame,
) -> tuple[tuple[str, Callable[[], npt.NDArray[Any]]], ...]:
    """Return fresh-access callables for every array in one frame."""
    return (
        ("keys", lambda: frame.keys),
        *(
            (name, lambda name=name: frame.column(name))
            for name in frame.column_names
        ),
    )


def _snapshot_frame(
    frame: MarketFrame,
) -> tuple[int, npt.NDArray[np.int64], dict[str, npt.NDArray[Any]]]:
    """Capture the complete public array state of one frame."""
    return (
        frame.num_rows,
        frame.keys,
        {name: frame.column(name) for name in frame.column_names},
    )


def _assert_frame_matches_snapshot(
    frame: MarketFrame,
    snapshot: tuple[int, npt.NDArray[np.int64], dict[str, npt.NDArray[Any]]],
) -> None:
    """Require fresh descriptors to retain all frame invariants and values."""
    expected_rows, expected_keys, expected_columns = snapshot
    assert frame.num_rows == expected_rows
    fresh_keys = frame.keys
    assert fresh_keys.shape == expected_keys.shape
    assert fresh_keys.dtype == expected_keys.dtype
    assert fresh_keys.strides == expected_keys.strides
    np.testing.assert_array_equal(fresh_keys, expected_keys)
    for name, expected in expected_columns.items():
        fresh = frame.column(name)
        assert fresh.shape == expected.shape
        assert fresh.dtype == expected.dtype
        assert fresh.strides == expected.strides
        np.testing.assert_array_equal(fresh, expected)


class TestMarketFrameSurface:
    def test_public_surface_is_deliberately_exact(self) -> None:
        public_names = {name for name in MarketFrame.__dict__ if not name.startswith("_")}
        assert public_names == {
            "dataset_ref",
            "clock_domain",
            "unit",
            "schema",
            "num_rows",
            "keys",
            "column_names",
            "column",
            "select",
            "slice_by_keys",
        }

    def test_no_row_or_computational_dunder_is_inherited(self) -> None:
        frame = _frame()
        for name in (
            "__iter__",
            "__getitem__",
            "__len__",
            "__contains__",
            "__array__",
            "__array_ufunc__",
            "__add__",
            "__sub__",
            "__mul__",
            "__truediv__",
        ):
            assert not hasattr(frame, name), f"{name} would expand the compact frame surface"

    def test_direct_construction_is_disabled(self) -> None:
        with pytest.raises(TypeError, match="data loader"):
            MarketFrame()

    def test_metadata_and_value_schema_are_exposed(self) -> None:
        frame = _frame()
        assert frame.dataset_ref == DATASET_REF
        assert frame.clock_domain == DOMAIN
        assert frame.unit is TimeUnit.SECONDS
        assert frame.schema == {"price": "float64", "volume": "uint64"}
        assert frame.column_names == ("price", "volume")
        assert frame.num_rows == 4
        assert "keys" not in frame.schema

    def test_frame_attributes_are_frozen(self) -> None:
        frame = _frame()
        with pytest.raises(AttributeError):
            frame.unit = TimeUnit.MILLISECONDS  # type: ignore[misc]

    def test_schema_is_read_only_and_copied_from_the_input(self) -> None:
        schema = {"price": "float64"}
        columns = {"price": np.array([1.0, 2.0, 3.0, 4.0])}
        frame = _frame(schema=schema, columns=columns)
        schema["later"] = "int64"
        mutable_schema = cast("dict[str, str]", frame.schema)
        with pytest.raises(TypeError):
            mutable_schema["price"] = "int64"
        assert frame.column_names == ("price",)


class TestMarketFrameConstruction:
    def test_initial_construction_copies_onto_immutable_backing(self) -> None:
        keys = np.array([10, 20, 30, 40], dtype=np.int64)
        price = np.array([1.0, 1.5, 2.0, 2.5], dtype=np.float64)
        volume = np.array([5, 6, 7, 8], dtype=np.uint64)
        frame = _frame(keys=keys, columns={"price": price, "volume": volume})
        assert not np.shares_memory(frame.keys, keys)
        assert not np.shares_memory(frame.column("price"), price)
        assert not np.shares_memory(frame.column("volume"), volume)
        assert keys.flags.writeable
        assert price.flags.writeable
        assert volume.flags.writeable
        assert not frame.keys.flags.owndata
        assert not frame.column("price").flags.owndata
        assert not frame.column("volume").flags.owndata
        assert not frame.keys.flags.writeable
        assert not frame.column("price").flags.writeable
        assert not frame.column("volume").flags.writeable
        with pytest.raises(ValueError, match="cannot set WRITEABLE flag"):
            frame.keys.setflags(write=True)
        with pytest.raises(ValueError, match="cannot set WRITEABLE flag"):
            frame.column("price").setflags(write=True)
        with pytest.raises(ValueError, match="cannot set WRITEABLE flag"):
            frame.column("volume").setflags(write=True)
        with pytest.raises(ValueError, match="read-only"):
            frame.keys[0] = 99
        with pytest.raises(ValueError, match="read-only"):
            frame.column("price")[0] = 99.0

        keys[0] = 99
        price[0] = 99.0
        volume[0] = 99
        assert frame.keys.tolist() == [10, 20, 30, 40]
        assert frame.column("price").tolist() == [1.0, 1.5, 2.0, 2.5]
        assert frame.column("volume").tolist() == [5, 6, 7, 8]

    def test_non_owning_inputs_are_detached_without_freezing_their_owner(self) -> None:
        key_owner = np.array([10, 0, 20, 0, 30, 0, 40, 0], dtype=np.int64)
        price_owner = np.array([1.0, 0.0, 1.5, 0.0, 2.0, 0.0, 2.5, 0.0])
        volume_owner = np.array([5, 0, 6, 0, 7, 0, 8, 0], dtype=np.uint64)
        frame = _frame(
            keys=key_owner[::2],
            columns={"price": price_owner[::2], "volume": volume_owner[::2]},
        )
        assert key_owner.flags.writeable
        assert price_owner.flags.writeable
        assert volume_owner.flags.writeable
        assert not np.shares_memory(frame.keys, key_owner)
        assert not np.shares_memory(frame.column("price"), price_owner)

    def test_every_public_array_and_derived_base_chain_has_no_writable_owner(
        self,
    ) -> None:
        original = _frame()
        frames = (
            original,
            original.select(("volume", "price")),
            original.slice_by_keys(_key(20), _key(30)),
            original.select(("price",)).slice_by_keys(_key(20), _key(30)),
            original.slice_by_keys(_key(20), _key(30)).select(("volume",)),
            original.select(()),
            original.slice_by_keys(_key(11), _key(19)),
        )

        for frame in frames:
            _assert_no_writable_ndarray_owner(frame.keys, sibling=original)
            for name in frame.column_names:
                _assert_no_writable_ndarray_owner(frame.column(name), sibling=original)

    def test_public_array_descriptors_are_isolated_across_every_frame_variant(
        self,
    ) -> None:
        original = _frame()
        frames = (
            original,
            original.select(("volume", "price")),
            original.slice_by_keys(_key(20), _key(30)),
            original.select(("price",)).slice_by_keys(_key(20), _key(30)),
            original.slice_by_keys(_key(20), _key(30)).select(("volume",)),
            original.select(()),
            original.slice_by_keys(_key(11), _key(19)),
        )
        snapshots = tuple(_snapshot_frame(frame) for frame in frames)

        for frame in frames:
            for label, access in _public_array_accessors(frame):
                first = access()
                second = access()
                assert first is not second, f"{label} leaked a shared ndarray descriptor"
                assert _immutable_buffer(first) is _immutable_buffer(second)
                if first.size:
                    assert np.shares_memory(first, second)

                attacked = access()
                with pytest.warns(DeprecationWarning, match="Setting the strides"):
                    attacked.strides = (0,)
                assert attacked.strides == (0,)
                for candidate, snapshot in zip(frames, snapshots, strict=True):
                    _assert_frame_matches_snapshot(candidate, snapshot)

                attacked = access()
                with pytest.warns(DeprecationWarning, match="Setting the shape"):
                    attacked.shape = (1, attacked.size)
                assert attacked.ndim == 2
                for candidate, snapshot in zip(frames, snapshots, strict=True):
                    _assert_frame_matches_snapshot(candidate, snapshot)

                attacked = access()
                with pytest.warns(DeprecationWarning, match="Setting the dtype"):
                    attacked.dtype = np.uint8  # type: ignore[misc, assignment]
                assert attacked.dtype == np.dtype(np.uint8)
                for candidate, snapshot in zip(frames, snapshots, strict=True):
                    _assert_frame_matches_snapshot(candidate, snapshot)

    def test_selected_and_sliced_frames_share_bytes_but_not_descriptors(self) -> None:
        original = _frame()
        selected = original.select(("price",))
        sliced = original.slice_by_keys(_key(20), _key(30))

        original_keys = original.keys
        selected_keys = selected.keys
        sliced_keys = sliced.keys
        assert original_keys is not selected_keys
        assert original_keys is not sliced_keys
        assert (
            _immutable_buffer(original_keys)
            is _immutable_buffer(selected_keys)
            is _immutable_buffer(sliced_keys)
        )
        assert np.shares_memory(original_keys, selected_keys)
        assert np.shares_memory(original_keys, sliced_keys)

        original_price = original.column("price")
        selected_price = selected.column("price")
        sliced_price = sliced.column("price")
        assert original_price is not selected_price
        assert original_price is not sliced_price
        assert (
            _immutable_buffer(original_price)
            is _immutable_buffer(selected_price)
            is _immutable_buffer(sliced_price)
        )
        assert np.shares_memory(original_price, selected_price)
        assert np.shares_memory(original_price, sliced_price)

    @pytest.mark.parametrize(
        "keys",
        [
            np.array([10, 10, 20, 30], dtype=np.int64),
            np.array([10, 30, 20, 40], dtype=np.int64),
        ],
    )
    def test_keys_must_be_strictly_increasing(self, keys: npt.NDArray[np.int64]) -> None:
        with pytest.raises(MarketFrameError, match="strictly increasing"):
            _frame(keys=keys)

    def test_keys_must_be_an_exact_one_dimensional_int64_array(self) -> None:
        with pytest.raises(MarketFrameError, match="NumPy ndarray"):
            _frame(keys=[10, 20, 30, 40])  # type: ignore[arg-type]
        with pytest.raises(MarketFrameError, match="one-dimensional"):
            _frame(keys=np.array([[10, 20], [30, 40]], dtype=np.int64))
        with pytest.raises(MarketFrameError, match="exact dtype int64"):
            _frame(keys=np.array([10, 20, 30, 40], dtype=np.int32))

    def test_schema_and_columns_must_have_exactly_the_same_names(self) -> None:
        with pytest.raises(MarketFrameError, match=r"missing=.*volume"):
            _frame(columns={"price": np.array([1.0, 2.0, 3.0, 4.0])})
        with pytest.raises(MarketFrameError, match=r"extra=.*spread"):
            _frame(
                schema={"price": "float64"},
                columns={
                    "price": np.array([1.0, 2.0, 3.0, 4.0]),
                    "spread": np.array([1, 2, 3, 4], dtype=np.int64),
                },
            )

    def test_value_columns_must_be_exact_aligned_one_dimensional_arrays(self) -> None:
        with pytest.raises(MarketFrameError, match="NumPy ndarray"):
            _frame(schema={"price": "float64"}, columns={"price": [1.0] * 4})  # type: ignore[dict-item]
        with pytest.raises(MarketFrameError, match="one-dimensional"):
            _frame(
                schema={"price": "float64"},
                columns={"price": np.ones((4, 1), dtype=np.float64)},
            )
        with pytest.raises(MarketFrameError, match="3 rows, expected 4"):
            _frame(
                schema={"price": "float64"},
                columns={"price": np.ones(3, dtype=np.float64)},
            )
        with pytest.raises(MarketFrameError, match="expected exact dtype float64"):
            _frame(
                schema={"price": "float64"},
                columns={"price": np.ones(4, dtype=np.float32)},
            )

    def test_schema_dtype_names_must_be_valid_and_canonical(self) -> None:
        column = np.ones(4, dtype=np.float64)
        with pytest.raises(MarketFrameError, match="not understood"):
            _frame(schema={"price": "not-a-dtype"}, columns={"price": column})
        with pytest.raises(MarketFrameError, match="canonical name"):
            _frame(schema={"price": "<f8"}, columns={"price": column})

    def test_object_and_masked_arrays_are_refused(self) -> None:
        with pytest.raises(MarketFrameError, match="must not contain Python objects"):
            _frame(
                schema={"price": "object"},
                columns={"price": np.array([1, 2, 3, 4], dtype=object)},
            )
        with pytest.raises(MarketFrameError, match="NumPy ndarray"):
            _frame(
                schema={"price": "float64"},
                columns={"price": np.ma.array([1.0, 2.0, 3.0, 4.0])},
            )

    def test_an_empty_frame_is_valid(self) -> None:
        frame = _frame(
            keys=np.array([], dtype=np.int64),
            schema={"price": "float64"},
            columns={"price": np.array([], dtype=np.float64)},
        )
        assert frame.num_rows == 0
        assert frame.keys.shape == (0,)

    def test_keys_must_be_inside_the_private_frame_bounds(self) -> None:
        with pytest.raises(MarketFrameError, match="keys exceed frame bounds"):
            _frame(admitted_lo=11)

    def test_private_frame_bounds_must_be_valid_int64_values(self) -> None:
        with pytest.raises(MarketFrameError, match="must be an integer"):
            _frame(admitted_lo=True)
        with pytest.raises(MarketFrameError, match="signed 64-bit"):
            _frame(admitted_hi=2**63)
        with pytest.raises(MarketFrameError, match="inverted"):
            _frame(admitted_lo=41, admitted_hi=40)


class TestMarketFrameColumns:
    def test_column_returns_a_fresh_read_only_descriptor(self) -> None:
        frame = _frame()
        first = frame.column("price")
        second = frame.column("price")
        assert first is not second
        assert np.shares_memory(first, second)
        assert first.tolist() == [1.0, 1.5, 2.0, 2.5]
        assert not first.flags.writeable

    def test_unknown_or_non_string_column_is_refused(self) -> None:
        frame = _frame()
        with pytest.raises(MarketFrameError, match="unknown"):
            frame.column("missing")
        with pytest.raises(MarketFrameError, match="must be a string"):
            frame.column(1)  # type: ignore[arg-type]

    def test_select_is_zero_copy_and_preserves_requested_order(self) -> None:
        frame = _frame()
        selected = frame.select(["volume", "price"])
        assert selected.column_names == ("volume", "price")
        assert np.shares_memory(selected.keys, frame.keys)
        assert np.shares_memory(selected.column("volume"), frame.column("volume"))
        assert np.shares_memory(selected.column("price"), frame.column("price"))
        with pytest.raises(ValueError, match="cannot set WRITEABLE flag"):
            selected.keys.setflags(write=True)
        with pytest.raises(ValueError, match="cannot set WRITEABLE flag"):
            selected.column("volume").setflags(write=True)

    def test_empty_select_retains_only_the_native_key_index(self) -> None:
        frame = _frame().select([])
        assert frame.column_names == ()
        assert frame.schema == {}
        assert frame.num_rows == 4
        assert frame.keys.tolist() == [10, 20, 30, 40]

    def test_invalid_selections_are_refused(self) -> None:
        frame = _frame()
        with pytest.raises(MarketFrameError, match="sequence"):
            frame.select("price")
        with pytest.raises(MarketFrameError, match="duplicate"):
            frame.select(["price", "price"])
        with pytest.raises(MarketFrameError, match="unknown"):
            frame.select(["missing"])
        with pytest.raises(MarketFrameError, match="must be a string"):
            frame.select([1])  # type: ignore[list-item]


class TestMarketFrameSlicing:
    def test_bounds_are_inclusive_and_result_shares_buffers(self) -> None:
        frame = _frame()
        sliced = frame.slice_by_keys(_key(20), _key(30))
        assert sliced.keys.tolist() == [20, 30]
        assert sliced.column("price").tolist() == [1.5, 2.0]
        assert np.shares_memory(sliced.keys, frame.keys)
        assert np.shares_memory(sliced.column("price"), frame.column("price"))
        assert not sliced.keys.flags.writeable
        assert not sliced.column("price").flags.writeable
        with pytest.raises(ValueError, match="cannot set WRITEABLE flag"):
            sliced.keys.setflags(write=True)
        with pytest.raises(ValueError, match="cannot set WRITEABLE flag"):
            sliced.column("price").setflags(write=True)

    def test_bounds_need_not_equal_observed_keys(self) -> None:
        frame = _frame().slice_by_keys(_key(11), _key(39))
        assert frame.keys.tolist() == [20, 30]

    def test_an_observed_key_gap_can_produce_an_empty_frame(self) -> None:
        frame = _frame().slice_by_keys(_key(11), _key(19))
        assert frame.num_rows == 0
        assert frame.column("price").shape == (0,)

    def test_slice_cannot_widen_the_private_frame_bounds(self) -> None:
        bounded = _frame().slice_by_keys(_key(11), _key(39))
        with pytest.raises(MarketFrameError, match="exceeds frame bounds"):
            bounded.slice_by_keys(_key(10), _key(39))

    def test_empty_slice_retains_its_private_frame_bounds(self) -> None:
        empty = _frame().slice_by_keys(_key(11), _key(19))
        assert empty.slice_by_keys(_key(12), _key(18)).num_rows == 0
        with pytest.raises(MarketFrameError, match="exceeds frame bounds"):
            empty.slice_by_keys(_key(10), _key(18))

    def test_inverted_bounds_are_refused(self) -> None:
        with pytest.raises(MarketFrameError, match="inverted"):
            _frame().slice_by_keys(_key(30), _key(20))

    def test_untagged_bounds_are_refused(self) -> None:
        with pytest.raises(MarketFrameError, match="NativeTimeKey"):
            _frame().slice_by_keys(10, _key(20))  # type: ignore[arg-type]

    def test_bounds_with_a_different_unit_are_refused(self) -> None:
        with pytest.raises(TimeUnitMismatchError, match="no implicit conversion"):
            _frame().slice_by_keys(
                _key(10, unit=TimeUnit.MILLISECONDS),
                _key(20, unit=TimeUnit.MILLISECONDS),
            )

    def test_bounds_with_a_different_domain_are_refused(self) -> None:
        with pytest.raises(ClockDomainError, match="no resolver"):
            _frame().slice_by_keys(
                _key(10, domain=OTHER_DOMAIN),
                _key(20, domain=OTHER_DOMAIN),
            )


def test_slice_1d_errors_have_small_useful_hierarchies() -> None:
    assert issubclass(IntegrityError, TradingResearchError)
    assert issubclass(PartitionReadError, TradingResearchError)
    assert issubclass(MarketFrameError, ContractViolationError)
    assert issubclass(UnsupportedMarketFrameRoleError, MarketFrameError)
