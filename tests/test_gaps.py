"""Arithmetic, immutability, and provenance-boundary tests for GapIndex."""

from __future__ import annotations

import json
from itertools import pairwise
from time import perf_counter
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from tests.load_fixtures import (
    GenerationCase,
    RecordingAuditWriter,
    make_generation_case,
)
from trading_research.core import (
    ClockDomain,
    ContractViolationError,
    DatasetRef,
    MarketFrame,
    NativeTimeKey,
    TimeUnit,
    ViewRole,
)
from trading_research.core.frames import _make_market_frame
from trading_research.data import (
    AccessReason,
    AccessRequest,
    DatasetRegistry,
    GapIndex,
    GatedUnlock,
    MarketFrameLoader,
    authorize_access,
)
from trading_research.data.manifests import read_manifest

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import numpy.typing as npt

_DIGEST = "a" * 64
_DOMAIN = ClockDomain("venue.wallclock")
_DATASET_REF = DatasetRef("SYNTH_TRAIN_V1", _DIGEST, ViewRole.TRAIN)
_ARRAY_PROPERTIES = ("prev_key", "next_key", "delta", "deviation")


def _frame(
    keys: Sequence[int] | npt.NDArray[np.int64],
    *,
    unit: TimeUnit = TimeUnit.SECONDS,
    with_values: bool = False,
    admitted_lo: int | None = None,
    admitted_hi: int | None = None,
) -> MarketFrame:
    """Mint one synthetic issued frame through the established private seam."""
    key_array = np.asarray(keys, dtype=np.int64)
    resolved_lo = (
        int(key_array[0]) if key_array.size else 0
    ) if admitted_lo is None else admitted_lo
    resolved_hi = (
        int(key_array[-1]) if key_array.size else 0
    ) if admitted_hi is None else admitted_hi
    schema = {"price": "float64"} if with_values else {}
    columns = (
        {"price": np.arange(key_array.size, dtype=np.float64)}
        if with_values
        else {}
    )
    return _make_market_frame(
        dataset_ref=_DATASET_REF,
        clock_domain=_DOMAIN,
        unit=unit,
        schema=schema,
        keys=key_array,
        columns=columns,
        admitted_lo=resolved_lo,
        admitted_hi=resolved_hi,
    )


def _arrays(index: GapIndex) -> tuple[npt.NDArray[np.int64], ...]:
    """Return the four aligned public arithmetic columns."""
    return tuple(getattr(index, name) for name in _ARRAY_PROPERTIES)


def _naive_reference(
    keys: npt.NDArray[np.int64], nominal_interval: int
) -> tuple[npt.NDArray[np.int64], ...]:
    """Simple test-side reference; production deliberately stays vectorized."""
    rows: list[tuple[int, int, int, int]] = []
    for previous, following in pairwise(keys):
        delta = int(following) - int(previous)
        if delta != nominal_interval:
            rows.append(
                (
                    int(previous),
                    int(following),
                    delta,
                    delta - nominal_interval,
                )
            )
    if not rows:
        empty = np.empty(0, dtype=np.int64)
        return empty, empty.copy(), empty.copy(), empty.copy()
    matrix = np.asarray(rows, dtype=np.int64)
    return tuple(matrix[:, column] for column in range(4))


def _assert_same_arithmetic(first: GapIndex, second: GapIndex) -> None:
    assert first.clock_domain == second.clock_domain
    assert first.unit is second.unit
    assert first.nominal_interval == second.nominal_interval
    assert first.num_gaps == second.num_gaps
    for first_array, second_array in zip(
        _arrays(first), _arrays(second), strict=True
    ):
        np.testing.assert_array_equal(first_array, second_array)


def _load_labeled_curated(
    case: GenerationCase,
    *,
    labels: dict[str, object],
) -> GapIndex:
    """Run one edited legacy CURATED variant through the real data interfaces."""
    manifest_path = case.curated_source.manifest_path
    document = json.loads(manifest_path.read_text())
    document.update(labels)
    manifest_path.write_text(json.dumps(document, indent=2))

    registry = DatasetRegistry.discover([case.source_root])
    manifest = read_manifest(manifest_path)
    ref = DatasetRef(
        dataset_id=manifest.dataset_id,
        manifest_sha256=manifest.manifest_sha256,
        role=manifest.role,
    )
    audit = RecordingAuditWriter()
    grant = authorize_access(
        registry=registry,
        request=AccessRequest(
            dataset_ref=ref,
            reason=AccessReason(
                code="synthetic-gap-label-proof",
                detail="Approved synthetic keys-only provenance non-promotion proof",
            ),
            gated_unlock=GatedUnlock(target=ref.canonical_identity),
        ),
        policy=case.policy,
        context=case.context,
        audit_writer=audit,
    )
    frame = MarketFrameLoader(registry=registry, split_policy=case.policy).load(
        grant,
        columns=(),
    )
    assert frame.column_names == ()
    assert len(audit.events) == 1
    return GapIndex.from_frame(frame, nominal_interval=1)


class TestGapArithmetic:
    def test_regular_sequence_is_empty(self) -> None:
        index = GapIndex.from_frame(_frame([0, 60, 120, 180]), nominal_interval=60)
        assert index.num_gaps == 0
        for array in _arrays(index):
            assert array.tolist() == []

    def test_one_gap_has_exact_four_field_values_and_metadata(self) -> None:
        frame = _frame([100, 160, 340, 400])
        index = GapIndex.from_frame(frame, nominal_interval=60)
        assert index.dataset_ref == frame.dataset_ref
        assert index.clock_domain == frame.clock_domain
        assert index.unit is TimeUnit.SECONDS
        assert index.nominal_interval == 60
        assert index.num_gaps == 1
        assert index.prev_key.tolist() == [160]
        assert index.next_key.tolist() == [340]
        assert index.delta.tolist() == [180]
        assert index.deviation.tolist() == [120]

    def test_multiple_gap_sizes_remain_ordered_and_aligned(self) -> None:
        index = GapIndex.from_frame(
            _frame([0, 60, 180, 240, 540, 570]),
            nominal_interval=60,
        )
        assert index.num_gaps == 3
        assert index.prev_key.tolist() == [60, 240, 540]
        assert index.next_key.tolist() == [180, 540, 570]
        assert index.delta.tolist() == [120, 300, 30]
        assert index.deviation.tolist() == [60, 240, -30]

    @pytest.mark.parametrize("nominal_interval", [60, 300, 900, 1])
    def test_multiple_nominal_intervals_are_used_exactly(
        self, nominal_interval: int
    ) -> None:
        keys = np.array(
            [0, nominal_interval, nominal_interval * 3],
            dtype=np.int64,
        )
        index = GapIndex.from_frame(
            _frame(keys),
            nominal_interval=nominal_interval,
        )
        assert index.prev_key.tolist() == [nominal_interval]
        assert index.next_key.tolist() == [nominal_interval * 3]
        assert index.delta.tolist() == [nominal_interval * 2]
        assert index.deviation.tolist() == [nominal_interval]

    def test_non_seconds_axis_uses_its_own_integer_unit(self) -> None:
        frame = _frame(
            [0, 60_000, 180_000],
            unit=TimeUnit.MILLISECONDS,
        )
        index = GapIndex.from_frame(frame, nominal_interval=60_000)
        assert index.unit is TimeUnit.MILLISECONDS
        assert index.prev_key.tolist() == [60_000]
        assert index.next_key.tolist() == [180_000]
        assert index.delta.tolist() == [120_000]
        assert index.deviation.tolist() == [60_000]

    def test_sub_nominal_transition_is_retained_with_negative_deviation(self) -> None:
        index = GapIndex.from_frame(_frame([0, 60, 90]), nominal_interval=60)
        assert index.delta.tolist() == [30]
        assert index.deviation.tolist() == [-30]

    def test_every_irregular_transition_is_retained(self) -> None:
        keys = np.array([0, 5, 12, 21, 32], dtype=np.int64)
        index = GapIndex.from_frame(_frame(keys), nominal_interval=10)
        assert index.num_gaps == keys.size - 1
        np.testing.assert_array_equal(index.prev_key, keys[:-1])
        np.testing.assert_array_equal(index.next_key, keys[1:])

    @pytest.mark.parametrize(
        ("keys", "expected_num_gaps"),
        [
            ([], 0),
            ([100], 0),
            ([100, 160], 0),
            ([100, 220], 1),
        ],
    )
    def test_zero_one_and_two_row_frames(
        self, keys: list[int], expected_num_gaps: int
    ) -> None:
        index = GapIndex.from_frame(_frame(keys), nominal_interval=60)
        assert index.num_gaps == expected_num_gaps
        assert all(array.shape == (expected_num_gaps,) for array in _arrays(index))

    def test_keys_only_frame_matches_full_column_frame(self) -> None:
        full = _frame([0, 60, 180, 240], with_values=True)
        full_index = GapIndex.from_frame(full, nominal_interval=60)
        keys_only_index = GapIndex.from_frame(full.select(()), nominal_interval=60)
        _assert_same_arithmetic(full_index, keys_only_index)

    def test_zero_observed_row_slice_produces_empty_index(self) -> None:
        frame = _frame([10, 20, 30, 40], with_values=True)
        empty = frame.slice_by_keys(
            NativeTimeKey(11, TimeUnit.SECONDS, _DOMAIN),
            NativeTimeKey(19, TimeUnit.SECONDS, _DOMAIN),
        )
        assert empty.num_rows == 0
        index = GapIndex.from_frame(empty, nominal_interval=10)
        assert index.num_gaps == 0
        assert all(array.shape == (0,) for array in _arrays(index))

    def test_value_columns_can_be_unusable_without_affecting_computation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        full = _frame([0, 60, 180, 240], with_values=True)
        expected = GapIndex.from_frame(full.select(()), nominal_interval=60)

        def poisoned_column(_frame: MarketFrame, _name: str) -> npt.NDArray[Any]:
            raise AssertionError("value-column access is forbidden")

        def poisoned_metadata(_frame: MarketFrame) -> object:
            raise AssertionError("value-column metadata access is forbidden")

        monkeypatch.setattr(MarketFrame, "column", poisoned_column)
        monkeypatch.setattr(MarketFrame, "column_names", property(poisoned_metadata))
        monkeypatch.setattr(MarketFrame, "schema", property(poisoned_metadata))
        with pytest.raises(AssertionError, match="value-column"):
            full.column("price")
        with pytest.raises(AssertionError, match="value-column"):
            _ = full.column_names

        observed = GapIndex.from_frame(full, nominal_interval=60)
        _assert_same_arithmetic(observed, expected)

    @pytest.mark.parametrize(
        ("seed", "size", "nominal_interval"),
        [
            (0, 1, 1),
            (1, 2, 60),
            (2, 32, 300),
            (3, 257, 7),
            (4, 1_024, 900),
        ],
    )
    def test_properties_soundness_and_completeness_against_naive_reference(
        self,
        seed: int,
        size: int,
        nominal_interval: int,
    ) -> None:
        rng = np.random.default_rng(seed)
        increments = rng.integers(
            1,
            nominal_interval * 3 + 2,
            size=max(0, size - 1),
            dtype=np.int64,
        )
        keys = np.empty(size, dtype=np.int64)
        if size:
            keys[0] = seed * 10_000
            np.cumsum(increments, out=keys[1:])
            keys[1:] += keys[0]

        index = GapIndex.from_frame(
            _frame(keys),
            nominal_interval=nominal_interval,
        )
        expected = _naive_reference(keys, nominal_interval)
        for observed, reference in zip(_arrays(index), expected, strict=True):
            np.testing.assert_array_equal(observed, reference)

        np.testing.assert_array_equal(index.delta, index.next_key - index.prev_key)
        np.testing.assert_array_equal(
            index.deviation,
            index.delta - nominal_interval,
        )
        adjacent_delta = np.diff(keys)
        retained = np.flatnonzero(adjacent_delta != nominal_interval)
        np.testing.assert_array_equal(index.prev_key, keys[:-1][retained])
        np.testing.assert_array_equal(index.next_key, keys[1:][retained])


class TestGapRepresentation:
    def test_columns_have_exact_shape_dtype_and_layout(self) -> None:
        index = GapIndex.from_frame(
            _frame([0, 60, 180, 240, 540]),
            nominal_interval=60,
        )
        for array in _arrays(index):
            assert array.dtype == np.dtype(np.int64)
            assert array.ndim == 1
            assert array.shape == (index.num_gaps,)
            assert array.flags.c_contiguous

    def test_empty_columns_preserve_exact_int64_contract(self) -> None:
        index = GapIndex.from_frame(_frame([]), nominal_interval=60)
        for array in _arrays(index):
            assert array.dtype == np.dtype(np.int64)
            assert array.ndim == 1
            assert array.shape == (0,)
            assert array.flags.c_contiguous

    def test_every_public_column_is_backed_by_immutable_bytes(self) -> None:
        index = GapIndex.from_frame(_frame([0, 60, 180]), nominal_interval=60)
        for array in _arrays(index):
            base: object = array.base
            assert isinstance(base, bytes)
            assert not array.flags.owndata
            assert not array.flags.writeable
            with pytest.raises(ValueError, match="cannot set WRITEABLE flag"):
                array.setflags(write=True)
            with pytest.raises(ValueError, match="read-only"):
                array[0] = 0

    def test_repeated_access_returns_fresh_descriptors_over_same_bytes(self) -> None:
        index = GapIndex.from_frame(
            _frame([0, 60, 180, 240, 540]),
            nominal_interval=60,
        )
        for name in _ARRAY_PROPERTIES:
            first = getattr(index, name)
            second = getattr(index, name)
            assert first is not second
            assert first.base is second.base
            assert np.shares_memory(first, second)

    def test_descriptor_mutation_is_isolated_from_fresh_access(self) -> None:
        index = GapIndex.from_frame(
            _frame([0, 60, 180, 240, 540]),
            nominal_interval=60,
        )
        snapshots = {name: getattr(index, name).copy() for name in _ARRAY_PROPERTIES}
        for name in _ARRAY_PROPERTIES:
            attacked = getattr(index, name)
            with pytest.warns(DeprecationWarning, match="Setting the strides"):
                attacked.strides = (0,)
            assert attacked.strides == (0,)

            attacked = getattr(index, name)
            with pytest.warns(DeprecationWarning, match="Setting the shape"):
                attacked.shape = (1, attacked.size)
            assert attacked.ndim == 2

            attacked = getattr(index, name)
            with pytest.warns(DeprecationWarning, match="Setting the dtype"):
                attacked.dtype = np.uint8
            assert attacked.dtype == np.dtype(np.uint8)

        for name, expected in snapshots.items():
            fresh = getattr(index, name)
            assert fresh.shape == (index.num_gaps,)
            assert fresh.dtype == np.dtype(np.int64)
            assert fresh.strides == (np.dtype(np.int64).itemsize,)
            np.testing.assert_array_equal(fresh, expected)


class TestGapRefusals:
    def test_direct_construction_is_refused(self) -> None:
        with pytest.raises(TypeError, match="from_frame"):
            GapIndex()

    def test_nominal_interval_is_required_and_keyword_only(self) -> None:
        frame = _frame([0, 60])
        with pytest.raises(TypeError, match="nominal_interval"):
            GapIndex.from_frame(frame)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            GapIndex.from_frame(frame, 60)  # type: ignore[call-arg]

    @pytest.mark.parametrize("invalid_frame", [None, object(), np.array([0, 60])])
    def test_invalid_frame_input_is_refused(self, invalid_frame: object) -> None:
        with pytest.raises(ContractViolationError, match="MarketFrame"):
            GapIndex.from_frame(
                invalid_frame,  # type: ignore[arg-type]
                nominal_interval=60,
            )

    def test_market_frame_subclass_is_refused_before_fabricated_keys_are_read(
        self,
    ) -> None:
        class FabricatedMarketFrame(MarketFrame):
            keys_requested = False

            @property
            def keys(self) -> npt.NDArray[np.float64]:  # type: ignore[override]
                type(self).keys_requested = True
                return np.array([0.0, 60.0], dtype=np.float64)

        fabricated = object.__new__(FabricatedMarketFrame)
        with pytest.raises(ContractViolationError, match="MarketFrame"):
            GapIndex.from_frame(fabricated, nominal_interval=60)
        assert not FabricatedMarketFrame.keys_requested

    @pytest.mark.parametrize("invalid", [True, False])
    def test_bool_nominal_interval_is_refused(self, invalid: bool) -> None:
        with pytest.raises(ContractViolationError, match="positive Python int"):
            GapIndex.from_frame(_frame([0, 60]), nominal_interval=invalid)

    @pytest.mark.parametrize("invalid", [60.0, "60", np.int64(60), None])
    def test_non_python_int_nominal_interval_is_refused(self, invalid: object) -> None:
        with pytest.raises(ContractViolationError, match="positive Python int"):
            GapIndex.from_frame(
                _frame([0, 60]),
                nominal_interval=invalid,  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize("invalid", [0, -1, -60])
    def test_non_positive_nominal_interval_is_refused(self, invalid: int) -> None:
        with pytest.raises(ContractViolationError, match="positive"):
            GapIndex.from_frame(_frame([0, 60]), nominal_interval=invalid)

    def test_nominal_interval_above_signed_int64_is_refused(self) -> None:
        invalid = int(np.iinfo(np.int64).max) + 1
        with pytest.raises(ContractViolationError, match="signed int64"):
            GapIndex.from_frame(_frame([0, 60]), nominal_interval=invalid)

    def test_signed_int64_maximum_nominal_interval_is_accepted_exactly(self) -> None:
        maximum = int(np.iinfo(np.int64).max)
        index = GapIndex.from_frame(
            _frame([0, maximum - 1]),
            nominal_interval=maximum,
        )
        assert index.nominal_interval == maximum
        assert index.prev_key.tolist() == [0]
        assert index.next_key.tolist() == [maximum - 1]
        assert index.delta.tolist() == [maximum - 1]
        assert index.deviation.tolist() == [-1]
        assert all(array.dtype == np.dtype(np.int64) for array in _arrays(index))

    def test_non_positive_wrapped_full_delta_is_refused_defensively(self) -> None:
        minimum = np.iinfo(np.int64).min
        maximum = np.iinfo(np.int64).max
        frame = _frame(
            np.array([minimum, maximum], dtype=np.int64),
            admitted_lo=int(minimum),
            admitted_hi=int(maximum),
        )
        with pytest.raises(ContractViolationError, match="int64 arithmetic"):
            GapIndex.from_frame(frame, nominal_interval=1)


def test_legacy_gap_provenance_labels_do_not_change_arithmetic_output(
    tmp_path: Path,
) -> None:
    first_case = make_generation_case(tmp_path / "first")
    second_case = make_generation_case(tmp_path / "second")
    first_document = json.loads(first_case.curated_source.manifest_path.read_text())
    second_document = json.loads(second_case.curated_source.manifest_path.read_text())
    assert first_document == second_document
    for first_path, second_path in zip(
        first_case.partition_paths,
        second_case.partition_paths,
        strict=True,
    ):
        assert first_path.read_bytes() == second_path.read_bytes()

    first = _load_labeled_curated(
        first_case,
        labels={
            "total_weekend_gap_count": 2,
            "total_intraweek_gap_count": 3,
            "gap_provenance": {
                "classification": "normal_boundary",
                "count": 5,
            },
        },
    )
    second = _load_labeled_curated(
        second_case,
        labels={
            "total_weekend_gap_count": 20_002,
            "total_intraweek_gap_count": 30_003,
            "gap_provenance": {
                "classification": "normal_boundary",
                "count": 50_005,
            },
        },
    )

    assert first.dataset_ref != second.dataset_ref
    _assert_same_arithmetic(first, second)


def test_synthetic_production_scale_representation_and_recomputation_smoke() -> None:
    num_keys = 2_328_717
    num_discontinuities = 2_200
    full_delta = np.full(num_keys - 1, 60, dtype=np.int64)
    positions = np.linspace(
        1,
        full_delta.size - 2,
        num=num_discontinuities,
        dtype=np.int64,
    )
    full_delta[positions] = 120
    keys = np.empty(num_keys, dtype=np.int64)
    keys[0] = 0
    np.cumsum(full_delta, out=keys[1:])
    frame = _frame(keys)

    started = perf_counter()
    index = GapIndex.from_frame(frame, nominal_interval=60)
    elapsed = perf_counter() - started

    assert index.num_gaps == num_discontinuities
    retained_bytes = sum(array.nbytes for array in _arrays(index))
    assert retained_bytes == num_discontinuities * 4 * np.dtype(np.int64).itemsize
    assert retained_bytes < 1_000_000
    assert elapsed < 1.0
