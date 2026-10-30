"""AccessGrant-only integrity loader tests over synthetic Parquet bytes."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NoReturn, cast

import numpy as np
import pytest

from tests.load_fixtures import (
    LoadCase,
    PartitionSpec,
    make_load_case,
    write_physical_partition,
    write_policy,
)
from trading_research.core.errors import (
    AccessGrantMismatchError,
    ClockDomainError,
    IntegrityError,
    MarketFrameError,
    PartitionReadError,
    TimeUnitMismatchError,
    UnsupportedMarketFrameRoleError,
)
from trading_research.core.identity import ViewRole
from trading_research.core.time import ClockDomain, NativeTimeKey, TimeUnit
from trading_research.data import MarketFrameLoader
from trading_research.data.gating import AccessGrant

if TYPE_CHECKING:
    from typing import BinaryIO


def _key(case: LoadCase, value: int) -> NativeTimeKey:
    return NativeTimeKey(
        value=value,
        unit=case.target.manifest.time_unit,
        clock_domain=case.target.manifest.clock_domain,
    )


def _loader(case: LoadCase) -> MarketFrameLoader:
    return MarketFrameLoader(
        registry=case.registry,
        split_policy=case.policy,
    )


class TestPublicLoadBoundary:
    def test_normal_grant_loads_without_audit(self, tmp_path: Path) -> None:
        case = make_load_case(tmp_path)

        frame = _loader(case).load(case.grant)

        assert frame.dataset_ref == case.grant.dataset_ref
        assert frame.keys.tolist() == [100, 101, 102, 200, 201, 202]
        assert frame.column_names == (
            "open",
            "high",
            "low",
            "close",
            "tick_volume",
            "spread",
            "real_volume",
        )
        assert frame.clock_domain == ClockDomain("mt5.synth-server.wallclock")
        assert frame.unit is TimeUnit.SECONDS
        assert not frame.keys.flags.writeable
        assert not frame.column("close").flags.writeable
        assert case.audit.events == []

    def test_loaded_and_zero_copy_derived_arrays_expose_no_writable_numpy_owner(
        self,
        tmp_path: Path,
    ) -> None:
        case = make_load_case(tmp_path)
        original = _loader(case).load(case.grant)
        variants = (
            original,
            original.select(("spread", "close", "tick_volume")),
            original.slice_by_keys(_key(case, 101), _key(case, 201)),
            original.select(("tick_volume", "spread")).slice_by_keys(
                _key(case, 101), _key(case, 201)
            ),
            original.slice_by_keys(_key(case, 101), _key(case, 201)).select(
                ("close",)
            ),
            original.select(()),
            original.slice_by_keys(_key(case, 150), _key(case, 160)),
        )
        original_keys = original.keys.copy()
        original_values = {
            name: original.column(name).copy() for name in original.column_names
        }

        for frame in variants:
            arrays = (frame.keys, *(frame.column(name) for name in frame.column_names))
            for array in arrays:
                current: object = array
                while isinstance(current, np.ndarray):
                    with pytest.raises(ValueError, match="cannot set WRITEABLE flag"):
                        current.setflags(write=True)
                    assert not current.flags.writeable
                    if current.size:
                        with pytest.raises(ValueError, match="read-only"):
                            current.flat[0] = 0
                    current = current.base

                np.testing.assert_array_equal(original.keys, original_keys)
                for name, expected in original_values.items():
                    np.testing.assert_array_equal(original.column(name), expected)

    @pytest.mark.parametrize(
        "role", [ViewRole.OOS, ViewRole.CURATED, ViewRole.SNAPSHOT, ViewRole.LOCKBOX]
    )
    def test_preissued_audited_non_raw_grants_load(self, tmp_path: Path, role: ViewRole) -> None:
        case = make_load_case(tmp_path, role=role)
        assert len(case.audit.events) == 1

        frame = _loader(case).load(case.grant, columns=("close",))

        assert frame.dataset_ref.role is role
        assert frame.column_names == ("close",)
        assert len(case.audit.events) == 1

    def test_valid_raw_grant_refuses_before_partition_access(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        case = make_load_case(tmp_path, role=ViewRole.RAW)

        def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
            raise AssertionError("RAW refusal must happen before partition access")

        monkeypatch.setattr(os, "open", forbidden)
        monkeypatch.setattr(integrity, "_open_partition_for_integrity", forbidden)
        monkeypatch.setattr(integrity, "_partition_size", forbidden)
        monkeypatch.setattr(integrity, "_sha256_partition", forbidden)
        monkeypatch.setattr(integrity, "_decode_partition", forbidden)
        monkeypatch.setattr(
            type(case.registry), "resolve_partition_source_location", forbidden
        )
        with pytest.raises(UnsupportedMarketFrameRoleError, match="RAW"):
            _loader(case).load(case.grant)

    @pytest.mark.parametrize(
        "invalid",
        [None, Path("target.parquet"), "already-authorized", object()],
    )
    def test_no_bare_or_asserted_target_route(self, tmp_path: Path, invalid: object) -> None:
        case = make_load_case(tmp_path)
        with pytest.raises(MarketFrameError, match="AccessGrant"):
            _loader(case).load(invalid)  # type: ignore[arg-type]

    def test_registered_metadata_objects_are_not_loading_authority(self, tmp_path: Path) -> None:
        case = make_load_case(tmp_path)
        invalid_targets = (
            case.grant.dataset_ref,
            case.grant.dataset_ref.canonical_identity,
            case.target,
            case.target.manifest,
        )
        for invalid in invalid_targets:
            with pytest.raises(MarketFrameError, match="AccessGrant"):
                _loader(case).load(invalid)  # type: ignore[arg-type]

    def test_uninitialized_grant_is_refused_before_partition_access(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        case = make_load_case(tmp_path)
        forged = object.__new__(AccessGrant)

        def forbidden_open(_path: Path) -> NoReturn:
            raise AssertionError("structurally invalid grant must refuse before I/O")

        monkeypatch.setattr(integrity, "_open_partition_for_integrity", forbidden_open)
        with pytest.raises(MarketFrameError, match="structurally complete"):
            _loader(case).load(forged)

    def test_existing_grant_loads_without_reauthorization_or_manifest_reread(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.gating as gating
        import trading_research.data.manifests as manifests

        case = make_load_case(tmp_path)

        def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
            raise AssertionError("loader must consume a grant, not reauthorize or reread")

        monkeypatch.setattr(gating, "authorize_access", forbidden)
        monkeypatch.setattr(gating, "classify_access", forbidden)
        monkeypatch.setattr(manifests, "read_manifest", forbidden)

        frame = _loader(case).load(case.grant, columns=("close",))
        assert frame.num_rows == 6

    def test_require_matches_is_immediately_before_first_partition_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        case = make_load_case(tmp_path)
        events: list[str] = []
        real_match = AccessGrant.require_matches
        real_open = integrity._open_partition_for_integrity

        def traced_match(self: AccessGrant, **kwargs: object) -> None:
            events.append("match")
            real_match(self, **kwargs)  # type: ignore[arg-type]

        def traced_open(path: Path) -> BinaryIO:
            events.append("open")
            return real_open(path)

        monkeypatch.setattr(AccessGrant, "require_matches", traced_match)
        monkeypatch.setattr(integrity, "_open_partition_for_integrity", traced_open)

        _loader(case).load(case.grant, columns=())
        assert events[0:2] == ["match", "open"]

    def test_mismatched_dataset_candidate_refuses_before_file_access(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity
        from trading_research.data.registry import DatasetRegistry

        case = make_load_case(tmp_path / "first")
        other = make_load_case(tmp_path / "second", view_id="OTHER_TRAIN_V1")
        real_resolve = DatasetRegistry.resolve
        source_location = case.registry.resolve_partition_source_location(case.target)

        def wrong_candidate(self: DatasetRegistry, ref: object) -> object:
            if self is case.registry:
                return other.target
            return real_resolve(self, ref)  # type: ignore[arg-type]

        def forbidden_open(_path: Path) -> NoReturn:
            raise AssertionError("mismatched grant must refuse before file access")

        monkeypatch.setattr(DatasetRegistry, "resolve", wrong_candidate)
        monkeypatch.setattr(
            DatasetRegistry,
            "resolve_partition_source_location",
            lambda _self, _target: source_location,
        )
        monkeypatch.setattr(integrity, "_open_partition_for_integrity", forbidden_open)
        with pytest.raises(AccessGrantMismatchError):
            _loader(case).load(case.grant)

    def test_mismatched_current_policy_refuses_before_file_access(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        case = make_load_case(tmp_path)
        other_policy = write_policy(tmp_path / "alternate-policy.json", alternate_bytes=True)

        def forbidden_open(_path: Path) -> NoReturn:
            raise AssertionError("policy mismatch must refuse before file access")

        monkeypatch.setattr(integrity, "_open_partition_for_integrity", forbidden_open)
        loader = MarketFrameLoader(registry=case.registry, split_policy=other_policy)
        with pytest.raises(AccessGrantMismatchError, match="split-policy"):
            loader.load(case.grant)


class TestIntegrityAndPruning:
    def test_duplicate_free_form_partition_labels_preserve_positional_file_binding(
        self, tmp_path: Path
    ) -> None:
        specs = (
            PartitionSpec("same", "first.parquet", (100, 101, 102), 100, 102, 3),
            PartitionSpec("same", "second.parquet", (200, 201, 202), 200, 202, 3),
        )
        case = make_load_case(tmp_path, specs=specs)
        frame = _loader(case).load(case.grant, columns=("close",))
        assert frame.keys.tolist() == [100, 101, 102, 200, 201, 202]

    def test_all_sizes_are_checked_before_the_first_hash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        case = make_load_case(tmp_path)
        events: list[str] = []
        real_size = integrity._partition_size
        real_hash = integrity._sha256_partition

        def traced_size(source: BinaryIO) -> int:
            events.append("size")
            return real_size(source)

        def traced_hash(source: BinaryIO) -> str:
            events.append("hash")
            return real_hash(source)

        monkeypatch.setattr(integrity, "_partition_size", traced_size)
        monkeypatch.setattr(integrity, "_sha256_partition", traced_hash)
        _loader(case).load(case.grant, columns=())
        assert events[:4] == ["size", "size", "hash", "hash"]

    def test_unselected_partition_is_hashed_but_never_decoded_or_materialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        mt5 = cast("Any", importlib.import_module("trading_research.data.adapters.mt5"))
        case = make_load_case(tmp_path)
        events: list[str] = []
        real_hash = integrity._sha256_partition
        real_parquet_file = mt5._pq.ParquetFile
        real_array_to_numpy = mt5._array_to_numpy

        def source_name(source: BinaryIO) -> str:
            descriptor_path = os.readlink(f"/proc/self/fd/{source.fileno()}")
            return Path(descriptor_path).name

        def traced_hash(source: BinaryIO) -> str:
            events.append(f"hash:{source_name(source)}")
            return real_hash(source)

        class TracedParquetFile:
            def __init__(self, source: BinaryIO) -> None:
                self._path_name = source_name(source)
                self._inner = real_parquet_file(source)

            @property
            def schema_arrow(self) -> object:
                return self._inner.schema_arrow

            def read(
                self, *, columns: list[str] | None = None, use_threads: bool = True
            ) -> object:
                events.append(f"decode:{self._path_name}:{columns!r}")
                return self._inner.read(columns=columns, use_threads=use_threads)

        def traced_materialization(
            array: object,
            expected: np.dtype[Any],
            *,
            column_name: str,
            path_label: str,
        ) -> object:
            events.append(f"numpy:{Path(path_label).name}:{column_name}")
            return real_array_to_numpy(
                array,
                expected,
                column_name=column_name,
                path_label=path_label,
            )

        monkeypatch.setattr(integrity, "_sha256_partition", traced_hash)
        monkeypatch.setattr(mt5._pq, "ParquetFile", TracedParquetFile)
        monkeypatch.setattr(mt5, "_array_to_numpy", traced_materialization)
        frame = _loader(case).load(
            case.grant,
            columns=("close",),
            lo=_key(case, 100),
            hi=_key(case, 102),
        )

        assert events == [
            "hash:target-0.parquet",
            "hash:target-1.parquet",
            "decode:target-0.parquet:['mt5_time_raw', 'close']",
            "numpy:target-0.parquet:mt5_time_raw",
            "numpy:target-0.parquet:close",
            "hash:target-0.parquet",
        ]
        assert not any(
            "target-1.parquet" in event
            for event in events
            if not event.startswith("hash:")
        )
        assert frame.keys.tolist() == [100, 101, 102]

    def test_same_inode_overwrite_during_load_is_detected_before_frame(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        case = make_load_case(tmp_path)
        first = case.partition_paths[0]
        original_inode = first.stat().st_ino
        real_hash = integrity._sha256_partition
        initial_hashes = 0

        def mutate_after_initial_full_integrity(source: BinaryIO) -> str:
            nonlocal initial_hashes
            observed = real_hash(source)
            initial_hashes += 1
            if initial_hashes == len(case.partition_paths):
                write_physical_partition(first, (100, 101, 102), close_offset=90.0)
                assert first.stat().st_ino == original_inode
            return observed

        monkeypatch.setattr(
            integrity,
            "_sha256_partition",
            mutate_after_initial_full_integrity,
        )
        with pytest.raises(IntegrityError, match="changed during decode"):
            _loader(case).load(case.grant, columns=("close",))

    def test_corrupt_unselected_target_file_fails_before_any_decode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        specs = (
            PartitionSpec("p0", "target-0.parquet", (100, 101, 102), 100, 102, 3),
            PartitionSpec(
                "p1",
                "target-1.parquet",
                (200, 201, 202),
                200,
                202,
                3,
                declared_sha256="f" * 64,
            ),
        )
        case = make_load_case(tmp_path, specs=specs)

        def forbidden_decode(*_args: object, **_kwargs: object) -> NoReturn:
            raise AssertionError("no partition may decode after any integrity failure")

        monkeypatch.setattr(integrity, "_decode_partition", forbidden_decode)
        with pytest.raises(IntegrityError, match=r"p1.*SHA-256"):
            _loader(case).load(
                case.grant,
                lo=_key(case, 100),
                hi=_key(case, 102),
            )

    def test_declared_size_mismatch_prevents_hash_and_decode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        specs = (
            PartitionSpec(
                "p0", "target-0.parquet", (100, 101, 102), 100, 102, 3
            ),
            PartitionSpec(
                "p1", "target-1.parquet", (200, 201, 202), 200, 202, 3, declared_size_delta=1
            ),
        )
        case = make_load_case(tmp_path, specs=specs)

        def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
            raise AssertionError("size mismatch must prevent hash and decode")

        monkeypatch.setattr(integrity, "_sha256_partition", forbidden)
        monkeypatch.setattr(integrity, "_decode_partition", forbidden)
        with pytest.raises(IntegrityError, match="size mismatch"):
            _loader(case).load(case.grant)

    def test_missing_target_size_is_not_filled_from_source(self, tmp_path: Path) -> None:
        case = make_load_case(tmp_path, include_target_sizes=False)
        assert all(partition.byte_size is None for partition in case.target.manifest.partitions)
        frame = _loader(case).load(case.grant, columns=())
        assert frame.num_rows == 6

    def test_source_manifest_partition_metadata_is_not_admission_authority(
        self, tmp_path: Path
    ) -> None:
        case = make_load_case(tmp_path)
        source_partition = next(
            entry
            for entry in case.registry.all_manifests_for_diagnostics()
            if entry.manifest.role is ViewRole.RAW
        ).manifest.partitions[0]
        assert source_partition.filename == "p0.parquet"
        assert {path.name for path in case.partition_paths} == {
            "target-0.parquet",
            "target-1.parquet",
        }

        frame = _loader(case).load(case.grant, columns=("close",))
        assert frame.num_rows == 6

    def test_target_column_and_partition_order_are_authoritative(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        specs = (
            PartitionSpec("early", "z.parquet", (100, 101, 102), 100, 102, 3),
            PartitionSpec("late", "a.parquet", (200, 201, 202), 200, 202, 3),
        )
        columns = [
            {"name": "native_key", "dtype": "int64", "purpose": "time_key"},
            {"name": "close", "dtype": "float64", "purpose": "value"},
            {"name": "open", "dtype": "float64", "purpose": "value"},
        ]
        case = make_load_case(tmp_path, specs=specs, target_columns=columns)
        decoded: list[str] = []
        real_decode = cast("Any", integrity._decode_partition)  # type: ignore[attr-defined]

        def traced_decode(
            source: BinaryIO,
            *,
            path_label: str,
            value_columns: tuple[str, ...],
            admitted_first_key: int,
            admitted_last_key: int,
        ) -> object:
            decoded.append(Path(path_label).name)
            return real_decode(
                source,
                path_label=path_label,
                value_columns=value_columns,
                admitted_first_key=admitted_first_key,
                admitted_last_key=admitted_last_key,
            )

        monkeypatch.setattr(integrity, "_decode_partition", traced_decode)
        frame = _loader(case).load(case.grant)

        assert frame.column_names == ("close", "open")
        assert decoded == ["z.parquet", "a.parquet"]
        assert frame.keys.tolist() == [100, 101, 102, 200, 201, 202]

    def test_stray_unlisted_file_is_unreachable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        case = make_load_case(tmp_path)
        stray = case.source_directory / "stray.parquet"
        stray.write_bytes(b"not parquet")
        opened: list[Path] = []
        real_open = integrity._open_partition_for_integrity

        def traced_open(path: Path) -> BinaryIO:
            opened.append(path)
            return real_open(path)

        monkeypatch.setattr(integrity, "_open_partition_for_integrity", traced_open)
        _loader(case).load(case.grant, columns=())
        assert stray not in opened
        assert opened == list(case.partition_paths)

    @pytest.mark.parametrize("failure", ["missing", "symlink"])
    def test_missing_or_symlink_partition_is_hard_refusal(
        self, tmp_path: Path, failure: str
    ) -> None:
        case = make_load_case(tmp_path)
        target = case.partition_paths[0]
        if failure == "missing":
            target.unlink()
        else:
            original = target.with_suffix(".original")
            target.rename(original)
            target.symlink_to(original)
        with pytest.raises(IntegrityError, match="safely open"):
            _loader(case).load(case.grant)

    def test_fifo_partition_is_rejected_without_blocking(self, tmp_path: Path) -> None:
        case = make_load_case(tmp_path)
        target = case.partition_paths[0]
        target.unlink()
        os.mkfifo(target)
        with pytest.raises(IntegrityError, match="not a regular file"):
            _loader(case).load(case.grant)


class TestAdmittedSliceBeforeCallerBounds:
    def test_wider_physical_file_is_restricted_to_target_admission(self, tmp_path: Path) -> None:
        spec = PartitionSpec("p0", "wide.parquet", (99, 100, 101, 102, 103), 100, 102, 3)
        case = make_load_case(tmp_path, specs=(spec,))

        frame = _loader(case).load(case.grant, columns=("close",))
        assert frame.keys.tolist() == [100, 101, 102]

    @pytest.mark.parametrize(
        "spec",
        [
            PartitionSpec("p0", "bad-count.parquet", (100, 101, 102), 100, 102, 4),
            PartitionSpec("p0", "missing-first.parquet", (101, 102, 103), 100, 103, 3),
            PartitionSpec(
                "p0", "duplicate.parquet", (100, 101, 101, 102, 103, 104, 105), 100, 105, 7
            ),
            PartitionSpec("p0", "unsorted.parquet", (100, 102, 101, 103, 104, 105), 100, 105, 6),
        ],
        ids=["row-count", "endpoint", "duplicate", "ordering"],
    )
    def test_defect_outside_narrow_request_still_refuses(
        self, tmp_path: Path, spec: PartitionSpec
    ) -> None:
        case = make_load_case(tmp_path, specs=(spec,))
        with pytest.raises(PartitionReadError):
            _loader(case).load(
                case.grant,
                columns=("close",),
                lo=_key(case, max(100, spec.last_key - 1)),
                hi=_key(case, spec.last_key),
            )

    def test_physical_schema_drift_refuses_without_casting(self, tmp_path: Path) -> None:
        case = make_load_case(tmp_path, spread_dtype=np.dtype("int64"))
        with pytest.raises(PartitionReadError, match="physical schema mismatch"):
            _loader(case).load(case.grant, columns=("spread",))

    def test_wrong_numpy_dtype_is_refused_without_silent_cast(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mt5 = cast("Any", importlib.import_module("trading_research.data.adapters.mt5"))
        real_array_to_numpy = mt5._array_to_numpy

        def wrong_float_dtype(
            array: object,
            expected: np.dtype[Any],
            *,
            column_name: str,
            path_label: str,
        ) -> object:
            values = real_array_to_numpy(
                array,
                expected,
                column_name=column_name,
                path_label=path_label,
            )
            if expected == np.dtype("float64"):
                return values.astype(np.float32)
            return values

        monkeypatch.setattr(mt5, "_array_to_numpy", wrong_float_dtype)
        case = make_load_case(tmp_path)
        with pytest.raises(PartitionReadError, match="dtype float32"):
            _loader(case).load(case.grant, columns=("close",))

    def test_target_dtype_drift_refuses_before_partition_io(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        columns = [
            {"name": "native_key", "dtype": "int64", "purpose": "time_key"},
            {"name": "spread", "dtype": "int64", "purpose": "value"},
        ]
        case = make_load_case(tmp_path, target_columns=columns)

        def forbidden_open(_path: Path) -> NoReturn:
            raise AssertionError("target schema drift must refuse before I/O")

        monkeypatch.setattr(integrity, "_open_partition_for_integrity", forbidden_open)
        with pytest.raises(PartitionReadError, match="requires 'int32'"):
            _loader(case).load(case.grant, columns=("spread",))

    @pytest.mark.parametrize(
        ("columns", "message"),
        [
            (
                [
                    {"name": "native_key", "dtype": "int64", "purpose": "time_key"},
                    {"name": "close", "dtype": "float64", "purpose": "value"},
                    {"name": "spread", "dtype": "float64", "purpose": "value"},
                ],
                "requires 'int32'",
            ),
            (
                [
                    {"name": "native_key", "dtype": "int64", "purpose": "time_key"},
                    {
                        "name": "timestamp_mt5_encoded",
                        "dtype": "int64",
                        "purpose": "display_only",
                    },
                    {"name": "close", "dtype": "float64", "purpose": "value"},
                ],
                "display-only timestamp dtype",
            ),
        ],
        ids=["unrequested-value", "display-only"],
    )
    def test_every_target_schema_declaration_is_validated_before_io(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        columns: list[dict[str, str]],
        message: str,
    ) -> None:
        import trading_research.data.integrity as integrity

        case = make_load_case(tmp_path, target_columns=columns)

        def forbidden_open(_path: Path) -> NoReturn:
            raise AssertionError("complete target schema must be checked before I/O")

        monkeypatch.setattr(integrity, "_open_partition_for_integrity", forbidden_open)
        with pytest.raises(PartitionReadError, match=message):
            _loader(case).load(case.grant, columns=("close",))

    def test_invalid_target_time_key_metadata_refuses_before_partition_io(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        columns = [
            {"name": "not_native", "dtype": "int64", "purpose": "time_key"},
            {"name": "close", "dtype": "float64", "purpose": "value"},
        ]
        case = make_load_case(tmp_path, target_columns=columns)

        def forbidden_open(_path: Path) -> NoReturn:
            raise AssertionError("invalid adapter metadata must refuse before I/O")

        monkeypatch.setattr(integrity, "_open_partition_for_integrity", forbidden_open)
        with pytest.raises(PartitionReadError, match="not an MT5 native-key"):
            _loader(case).load(case.grant, columns=("close",))

    def test_target_adapter_is_authoritative_and_validated_before_io(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        case = make_load_case(tmp_path, target_adapter_id="not-mt5")

        def forbidden_open(_path: Path) -> NoReturn:
            raise AssertionError("target adapter metadata must be checked before I/O")

        monkeypatch.setattr(integrity, "_open_partition_for_integrity", forbidden_open)
        with pytest.raises(PartitionReadError, match="unsupported market-data adapter"):
            _loader(case).load(case.grant, columns=("close",))

    def test_null_outside_narrow_request_still_refuses(self, tmp_path: Path) -> None:
        spec = PartitionSpec("p0", "null.parquet", (100, 101, 102, 103, 104, 105), 100, 105, 6)
        case = make_load_case(tmp_path, specs=(spec,), null_close_index=1)
        with pytest.raises(PartitionReadError, match="contains nulls"):
            _loader(case).load(
                case.grant,
                columns=("close",),
                lo=_key(case, 104),
                hi=_key(case, 105),
            )

    def test_null_outside_target_admitted_span_is_ignored(self, tmp_path: Path) -> None:
        spec = PartitionSpec("p0", "wide-null.parquet", (99, 100, 101, 102), 100, 102, 3)
        case = make_load_case(tmp_path, specs=(spec,), null_close_index=0)

        frame = _loader(case).load(case.grant, columns=("close",))

        assert frame.keys.tolist() == [100, 101, 102]
        assert frame.column("close").tolist() == [2.5, 3.5, 4.5]

    @pytest.mark.parametrize("column", ["tick_volume", "spread"])
    def test_nullable_integer_outside_target_admission_does_not_poison_load(
        self,
        tmp_path: Path,
        column: str,
    ) -> None:
        spec = PartitionSpec(
            "p0",
            f"wide-null-{column}.parquet",
            (99, 100, 101, 102),
            100,
            102,
            3,
        )
        if column == "tick_volume":
            case = make_load_case(
                tmp_path,
                specs=(spec,),
                null_tick_volume_index=0,
            )
        else:
            case = make_load_case(
                tmp_path,
                specs=(spec,),
                null_spread_index=0,
            )

        frame = _loader(case).load(case.grant, columns=(column,))

        assert frame.keys.tolist() == [100, 101, 102]
        assert frame.column(column).tolist() == (
            [11, 12, 13] if column == "tick_volume" else [1, 2, 3]
        )

    @pytest.mark.parametrize("column", ["tick_volume", "spread"])
    def test_nullable_integer_inside_target_admission_fails_closed(
        self,
        tmp_path: Path,
        column: str,
    ) -> None:
        spec = PartitionSpec(
            "p0",
            f"admitted-null-{column}.parquet",
            (99, 100, 101, 102),
            100,
            102,
            3,
        )
        if column == "tick_volume":
            case = make_load_case(
                tmp_path,
                specs=(spec,),
                null_tick_volume_index=2,
            )
        else:
            case = make_load_case(
                tmp_path,
                specs=(spec,),
                null_spread_index=2,
            )

        with pytest.raises(PartitionReadError, match=rf"column '{column}'.*nulls"):
            _loader(case).load(case.grant, columns=(column,))

    def test_inconsistent_decoded_column_length_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        case = make_load_case(
            tmp_path, specs=(PartitionSpec("p0", "short.parquet", (100, 101, 102), 100, 102, 3),)
        )
        decoded = SimpleNamespace(
            keys=np.array([100, 101, 102], dtype=np.int64),
            columns={"close": np.array([1.0, 2.0], dtype=np.float64)},
        )
        monkeypatch.setattr(integrity, "_decode_partition", lambda *_args, **_kwargs: decoded)
        with pytest.raises(PartitionReadError, match="length/shape"):
            _loader(case).load(case.grant, columns=("close",))

    def test_late_decode_failure_never_constructs_a_partial_frame(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        case = make_load_case(tmp_path)
        real_decode = cast("Any", integrity._decode_partition)  # type: ignore[attr-defined]
        calls = 0

        def fail_second(
            source: BinaryIO,
            *,
            path_label: str,
            value_columns: tuple[str, ...],
            admitted_first_key: int,
            admitted_last_key: int,
        ) -> object:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PartitionReadError("fault-injected second-partition failure")
            return real_decode(
                source,
                path_label=path_label,
                value_columns=value_columns,
                admitted_first_key=admitted_first_key,
                admitted_last_key=admitted_last_key,
            )

        def forbidden_frame(*_args: object, **_kwargs: object) -> NoReturn:
            raise AssertionError("a failed load must never construct a partial frame")

        monkeypatch.setattr(integrity, "_decode_partition", fail_second)
        monkeypatch.setattr(integrity, "_make_market_frame", forbidden_frame)
        with pytest.raises(PartitionReadError, match="second-partition"):
            _loader(case).load(case.grant, columns=("close",))


class TestBoundsAndColumnProjection:
    def test_parquet_boundary_projects_only_key_and_requested_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mt5 = cast("Any", importlib.import_module("trading_research.data.adapters.mt5"))
        real_parquet_file = mt5._pq.ParquetFile
        reads: list[tuple[list[str] | None, bool]] = []

        class TracedParquetFile:
            def __init__(self, source: BinaryIO) -> None:
                self._inner = real_parquet_file(source)

            @property
            def schema_arrow(self) -> object:
                return self._inner.schema_arrow

            def read(
                self, *, columns: list[str] | None = None, use_threads: bool = True
            ) -> object:
                reads.append((columns, use_threads))
                return self._inner.read(columns=columns, use_threads=use_threads)

        monkeypatch.setattr(mt5._pq, "ParquetFile", TracedParquetFile)
        selected = make_load_case(tmp_path / "selected")
        _loader(selected).load(selected.grant, columns=("close",))
        keys_only = make_load_case(tmp_path / "keys-only")
        _loader(keys_only).load(keys_only.grant, columns=())

        assert reads == [
            (["mt5_time_raw", "close"], False),
            (["mt5_time_raw", "close"], False),
            (["mt5_time_raw"], False),
            (["mt5_time_raw"], False),
        ]
        assert all("timestamp_mt5_encoded" not in (columns or []) for columns, _ in reads)

    @pytest.mark.parametrize(
        "columns",
        [
            ("close", "close"),
            ("missing",),
            ("timestamp_mt5_encoded",),
            ("native_key",),
        ],
    )
    def test_invalid_column_requests_refuse_before_io(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        columns: tuple[str, ...],
    ) -> None:
        import trading_research.data.integrity as integrity

        case = make_load_case(tmp_path)

        def forbidden_open(_path: Path) -> NoReturn:
            raise AssertionError("invalid column request must refuse before I/O")

        monkeypatch.setattr(integrity, "_open_partition_for_integrity", forbidden_open)
        with pytest.raises(MarketFrameError):
            _loader(case).load(case.grant, columns=columns)

    def test_keys_only_load_projects_no_values(self, tmp_path: Path) -> None:
        case = make_load_case(tmp_path)
        frame = _loader(case).load(case.grant, columns=())
        assert frame.column_names == ()
        assert frame.schema == {}
        assert frame.keys.tolist() == [100, 101, 102, 200, 201, 202]

    def test_valid_gap_interval_can_return_empty_frame(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trading_research.data.integrity as integrity

        case = make_load_case(tmp_path)
        decoded: list[str] = []
        real_decode = cast("Any", integrity._decode_partition)  # type: ignore[attr-defined]

        def traced_decode(
            source: BinaryIO,
            *,
            path_label: str,
            value_columns: tuple[str, ...],
            admitted_first_key: int,
            admitted_last_key: int,
        ) -> object:
            decoded.append(path_label)
            return real_decode(
                source,
                path_label=path_label,
                value_columns=value_columns,
                admitted_first_key=admitted_first_key,
                admitted_last_key=admitted_last_key,
            )

        monkeypatch.setattr(integrity, "_decode_partition", traced_decode)
        frame = _loader(case).load(
            case.grant,
            columns=("close",),
            lo=_key(case, 150),
            hi=_key(case, 160),
        )
        assert frame.num_rows == 0
        assert frame.column("close").dtype == np.dtype("float64")
        assert decoded == []

    @pytest.mark.parametrize("kind", ["inverted", "outside-low", "outside-high"])
    def test_invalid_bounds_refuse_before_io(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        import trading_research.data.integrity as integrity

        case = make_load_case(tmp_path)
        bounds = {
            "inverted": (_key(case, 102), _key(case, 101)),
            "outside-low": (_key(case, 99), _key(case, 101)),
            "outside-high": (_key(case, 200), _key(case, 203)),
        }[kind]

        def forbidden_open(_path: Path) -> NoReturn:
            raise AssertionError("invalid bounds must refuse before I/O")

        monkeypatch.setattr(integrity, "_open_partition_for_integrity", forbidden_open)
        with pytest.raises(MarketFrameError):
            _loader(case).load(case.grant, lo=bounds[0], hi=bounds[1])

    def test_cross_domain_and_unit_bounds_refuse_before_io(self, tmp_path: Path) -> None:
        case = make_load_case(tmp_path)
        loader = _loader(case)
        wrong_domain = NativeTimeKey(100, TimeUnit.SECONDS, ClockDomain("other.wallclock"))
        wrong_unit = NativeTimeKey(
            100, TimeUnit.MILLISECONDS, ClockDomain("mt5.synth-server.wallclock")
        )
        with pytest.raises(ClockDomainError):
            loader.load(case.grant, lo=wrong_domain)
        with pytest.raises(TimeUnitMismatchError):
            loader.load(case.grant, lo=wrong_unit)
