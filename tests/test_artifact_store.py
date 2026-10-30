"""Slice 2E crash-safe immutable feature artifact storage."""

from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

import trading_research.artifacts.store as store_module
from trading_research.artifacts import ArtifactStore, PlatformIdentity, StageIdentity
from trading_research.artifacts.container import _encode_feature_artifact
from trading_research.core import (
    ClockDomain,
    CostHint,
    DatasetIdentity,
    DatasetRef,
    FeatureFrame,
    FeatureOrigin,
    FeatureRequest,
    GapPolicy,
    NativeTimeKey,
    ParamValue,
    TimeUnit,
    TradingResearchError,
    ViewRole,
    WindowSpec,
)
from trading_research.core.feature_frames import _make_feature_frame
from trading_research.core.frames import MarketFrame, _make_market_frame

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    import numpy.typing as npt

_DOMAIN = ClockDomain("artifact-store.wallclock")
_SOURCES = (
    DatasetIdentity("STORE_CONTEXT", "a" * 64),
    DatasetIdentity("STORE_EVALUATION", "b" * 64),
)


@dataclass(frozen=True, slots=True)
class _Provider:
    provider_id: str = "store_provider"
    version: int = 3
    feature_names: tuple[str, ...] = ("store.value", "../safe-name")
    required_columns: tuple[str, ...] = ("close",)
    window: WindowSpec = field(default_factory=lambda: WindowSpec(1, GapPolicy.CROSS))
    params: Mapping[str, ParamValue] = field(
        default_factory=lambda: {"scale": 1.5, "flags": (True, -0.0)}
    )
    cost_hint: CostHint = CostHint.CHEAP

    def compute(
        self,
        request: FeatureRequest,
    ) -> Mapping[str, npt.NDArray[np.float64]]:
        raise AssertionError(f"artifact fixture called provider compute with {request!r}")


@dataclass(frozen=True, slots=True)
class _Case:
    identity: StageIdentity
    frame: FeatureFrame
    payload: bytes

    @property
    def key(self) -> str:
        return self.identity.identity_key()

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


def _key(value: int, *, domain: ClockDomain = _DOMAIN) -> NativeTimeKey:
    return NativeTimeKey(value, TimeUnit.SECONDS, domain)


def _market_frame(
    keys: Sequence[int],
    *,
    source: DatasetIdentity,
    role: ViewRole,
    domain: ClockDomain,
) -> MarketFrame:
    key_array = np.asarray(keys, dtype=np.int64)
    close = np.arange(100.0, 100.0 + key_array.size, dtype=np.float64)
    return _make_market_frame(
        dataset_ref=DatasetRef(source.dataset_id, source.manifest_sha256, role),
        clock_domain=domain,
        unit=TimeUnit.SECONDS,
        schema={"close": "float64"},
        keys=key_array,
        columns={"close": close},
        admitted_lo=int(key_array[0]),
        admitted_hi=int(key_array[-1]),
    )


def _request(
    *,
    sources: tuple[DatasetIdentity, DatasetIdentity],
    domain: ClockDomain,
) -> FeatureRequest:
    frames = (
        _market_frame((10, 20), source=sources[0], role=ViewRole.TRAIN, domain=domain),
        _market_frame(
            (30, 40, 50),
            source=sources[1],
            role=ViewRole.VALIDATION,
            domain=domain,
        ),
    )
    return FeatureRequest.from_segments(
        frames,
        eval_start=_key(30, domain=domain),
        eval_end=_key(50, domain=domain),
        required_columns=("close",),
    )


def _case(
    *,
    first_value: float = 1.0,
    sources: tuple[DatasetIdentity, DatasetIdentity] = _SOURCES,
    domain: ClockDomain = _DOMAIN,
) -> _Case:
    provider = _Provider()
    request = _request(sources=sources, domain=domain)
    origin = FeatureOrigin(
        provider.provider_id,
        provider.version,
        provider.params,
        provider.window,
    )
    columns = OrderedDict(
        (
            (
                provider.feature_names[0],
                np.asarray([first_value, -0.0, np.nan], dtype=np.float64),
            ),
            (
                provider.feature_names[1],
                np.asarray([2.0, 3.0, 4.0], dtype=np.float64),
            ),
        )
    )
    frame = _make_feature_frame(
        request=request,
        columns=columns,
        origins=OrderedDict((name, origin) for name in columns),
    )
    identity = StageIdentity(
        source_identities=sources,
        resolved_context_start=_key(10, domain=domain),
        eval_start=_key(30, domain=domain),
        eval_end=_key(50, domain=domain),
        split_policy_adr="ADR-0012",
        split_policy_version="v1",
        split_policy_sha256="c" * 64,
        providers=(provider,),
        platform_identity=PlatformIdentity("d" * 40, False, None),
    )
    return _Case(identity, frame, _encode_feature_artifact(identity, frame))


def _identity_directory(root: Path, key: str, *, create: bool = False) -> Path:
    result = root / "cache" / "features" / key
    if create:
        result.mkdir(parents=True, exist_ok=True)
    return result


def _finals(root: Path, key: str) -> tuple[Path, ...]:
    directory = _identity_directory(root, key)
    return tuple(sorted(directory.glob("*.npz"))) if directory.is_dir() else ()


def _temps(root: Path, key: str) -> tuple[Path, ...]:
    directory = _identity_directory(root, key)
    return tuple(sorted(directory.glob(".feature-artifact-*.tmp"))) if directory.is_dir() else ()


def _plant(root: Path, key: str, payload: bytes, *, name: str | None = None) -> Path:
    directory = _identity_directory(root, key, create=True)
    digest = hashlib.sha256(payload).hexdigest()
    path = directory / (f"{digest}.npz" if name is None else name)
    path.write_bytes(payload)
    return path


def test_constructor_and_true_miss_have_no_write_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    store = ArtifactStore(tmp_path)
    calls: list[tuple[object, ...]] = []
    actual_mkdir = store_module._make_dir_at

    def recording_mkdir(*args: object, **kwargs: object) -> None:
        calls.append((*args, kwargs))
        actual_mkdir(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store_module, "_make_dir_at", recording_mkdir)

    assert store.get(case.key) is None
    assert calls == []
    assert tuple(tmp_path.iterdir()) == ()


def test_put_uses_exact_layout_whole_file_hash_and_verified_get(tmp_path: Path) -> None:
    case = _case()
    store = ArtifactStore(tmp_path)

    observed_hash = store.put(case.key, case.payload)
    expected = _identity_directory(tmp_path, case.key) / f"{case.content_hash}.npz"

    assert observed_hash == case.content_hash
    assert expected.read_bytes() == case.payload
    assert _finals(tmp_path, case.key) == (expected,)
    assert store.get(case.key) == case.payload


def test_idempotent_republish_does_not_rewrite_or_allocate_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    store = ArtifactStore(tmp_path)
    assert store.put(case.key, case.payload) == case.content_hash
    final = _finals(tmp_path, case.key)[0]
    before = final.stat()

    def forbidden_temp(*_args: object, **_kwargs: object) -> tuple[str, int]:
        raise AssertionError("idempotent publication allocated a temp")

    monkeypatch.setattr(store_module, "_create_private_temp", forbidden_temp)

    assert store.put(case.key, case.payload) == case.content_hash
    after = final.stat()
    assert (after.st_ino, after.st_mtime_ns, final.read_bytes()) == (
        before.st_ino,
        before.st_mtime_ns,
        case.payload,
    )
    assert _temps(tmp_path, case.key) == ()


def test_get_and_put_take_shared_and_exclusive_identity_locks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    store = ArtifactStore(tmp_path)
    store.put(case.key, case.payload)
    actual_lock = store_module._lock_fd
    operations: list[int] = []

    def recording_lock(descriptor: int, operation: int) -> None:
        operations.append(operation)
        actual_lock(descriptor, operation)

    monkeypatch.setattr(store_module, "_lock_fd", recording_lock)

    assert store.get(case.key) == case.payload
    assert store.put(case.key, case.payload) == case.content_hash
    assert operations == [
        fcntl.LOCK_SH,
        fcntl.LOCK_UN,
        fcntl.LOCK_EX,
        fcntl.LOCK_UN,
    ]


@pytest.mark.parametrize(
    "bad_key",
    [
        None,
        b"a" * 64,
        "",
        ".",
        "..",
        "../escape",
        "/absolute",
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        "a" * 32 + "/" + "b" * 31,
        "a" * 32 + "\\" + "b" * 31,
    ],
    ids=[
        "none",
        "bytes",
        "empty",
        "dot",
        "dotdot",
        "relative-traversal",
        "absolute",
        "short",
        "long",
        "uppercase",
        "nonhex",
        "slash",
        "backslash",
    ],
)
def test_invalid_keys_refuse_before_filesystem_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_key: object,
) -> None:
    case = _case()

    def forbidden_open(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("invalid key reached filesystem access")

    monkeypatch.setattr(store_module, "_open_fd", forbidden_open)
    store = ArtifactStore(tmp_path)

    with pytest.raises(TradingResearchError):
        store.get(cast("str", bad_key))
    with pytest.raises(TradingResearchError):
        store.put(cast("str", bad_key), case.payload)


@pytest.mark.parametrize("bad_payload", [bytearray(b"x"), memoryview(b"x"), "x"])
def test_nonexact_bytes_refuse_before_filesystem_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_payload: object,
) -> None:
    case = _case()

    def forbidden_open(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("invalid payload reached filesystem access")

    monkeypatch.setattr(store_module, "_open_fd", forbidden_open)
    with pytest.raises(TradingResearchError):
        ArtifactStore(tmp_path).put(case.key, cast("bytes", bad_payload))


def test_malformed_and_mismatched_payloads_refuse_before_filesystem_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    other = _case(
        sources=(
            DatasetIdentity("OTHER_CONTEXT", "e" * 64),
            DatasetIdentity("OTHER_EVALUATION", "f" * 64),
        ),
        domain=ClockDomain("other-artifact-store.wallclock"),
    )

    def forbidden_open(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("unvalidated bytes reached filesystem access")

    monkeypatch.setattr(store_module, "_open_fd", forbidden_open)
    store = ArtifactStore(tmp_path)

    for payload in (b"not-an-npz", case.payload[: len(case.payload) // 2], other.payload):
        with pytest.raises(TradingResearchError):
            store.put(case.key, payload)
    assert tuple(tmp_path.iterdir()) == ()


@pytest.mark.parametrize(
    "kind",
    [
        "truncated",
        "flipped",
        "wrong-hash-name",
        "wrong-magic-correct-hash",
        "identity-mismatch",
        "malformed-final-name",
    ],
)
def test_corruption_is_always_a_loud_refusal(
    tmp_path: Path,
    kind: str,
) -> None:
    case = _case()
    if kind == "truncated":
        _plant(
            tmp_path,
            case.key,
            case.payload[: len(case.payload) // 2],
            name=f"{case.content_hash}.npz",
        )
    elif kind == "flipped":
        damaged = bytearray(case.payload)
        damaged[len(damaged) // 2] ^= 1
        _plant(
            tmp_path,
            case.key,
            bytes(damaged),
            name=f"{case.content_hash}.npz",
        )
    elif kind == "wrong-hash-name":
        _plant(tmp_path, case.key, case.payload, name=f"{'0' * 64}.npz")
    elif kind == "wrong-magic-correct-hash":
        _plant(tmp_path, case.key, b"not-an-npz")
    elif kind == "identity-mismatch":
        other = _case(
            sources=(
                DatasetIdentity("OTHER_CONTEXT", "e" * 64),
                DatasetIdentity("OTHER_EVALUATION", "f" * 64),
            ),
            domain=ClockDomain("other-store.wallclock"),
        )
        _plant(tmp_path, case.key, other.payload)
    else:
        _plant(tmp_path, case.key, case.payload, name="malformed.npz")

    with pytest.raises(TradingResearchError):
        ArtifactStore(tmp_path).get(case.key)


def test_hash_mismatch_refuses_before_container_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    malformed = b"not-an-npz"
    _plant(tmp_path, case.key, malformed, name=f"{'0' * 64}.npz")
    decoder_calls = 0

    def forbidden_decode(*_args: object, **_kwargs: object) -> None:
        nonlocal decoder_calls
        decoder_calls += 1
        raise AssertionError("hash-mismatched bytes reached container decode")

    monkeypatch.setattr(store_module, "_decode_feature_artifact", forbidden_decode)

    with pytest.raises(TradingResearchError):
        ArtifactStore(tmp_path).get(case.key)
    assert decoder_calls == 0


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory"])
def test_nonregular_final_entry_refuses_without_following(
    tmp_path: Path,
    kind: str,
) -> None:
    case = _case()
    directory = _identity_directory(tmp_path, case.key, create=True)
    final = directory / f"{case.content_hash}.npz"
    outside = tmp_path / "outside.bin"
    outside.write_bytes(case.payload)
    if kind == "symlink":
        final.symlink_to(outside)
    elif kind == "fifo":
        os.mkfifo(final)
    else:
        final.mkdir()

    with pytest.raises(TradingResearchError):
        ArtifactStore(tmp_path).get(case.key)
    assert outside.read_bytes() == case.payload


def test_stray_nonfinal_files_are_inert_and_never_deleted(tmp_path: Path) -> None:
    case = _case()
    directory = _identity_directory(tmp_path, case.key, create=True)
    stray = {
        ".feature-artifact-stray.tmp": b"torn",
        "noise": b"noise",
        f"{case.content_hash}.NPZ": b"upper-case extension is not final",
    }
    for name, contents in stray.items():
        (directory / name).write_bytes(contents)
    store = ArtifactStore(tmp_path)

    assert store.get(case.key) is None
    assert store.put(case.key, case.payload) == case.content_hash
    assert store.get(case.key) == case.payload
    assert {name: (directory / name).read_bytes() for name in stray} == stray


def test_two_entries_under_one_identity_are_a_hard_conflict(tmp_path: Path) -> None:
    first = _case(first_value=1.0)
    second = _case(first_value=9.0)
    assert first.key == second.key
    assert first.content_hash != second.content_hash
    _plant(tmp_path, first.key, first.payload)
    _plant(tmp_path, second.key, second.payload)
    store = ArtifactStore(tmp_path)

    with pytest.raises(TradingResearchError):
        store.get(first.key)
    with pytest.raises(TradingResearchError):
        store.put(first.key, first.payload)


def test_sequential_different_content_never_overwrites_winner(tmp_path: Path) -> None:
    first = _case(first_value=1.0)
    second = _case(first_value=9.0)
    store = ArtifactStore(tmp_path)
    assert store.put(first.key, first.payload) == first.content_hash
    inventory = _finals(tmp_path, first.key)

    with pytest.raises(TradingResearchError):
        store.put(second.key, second.payload)

    assert _finals(tmp_path, first.key) == inventory
    assert store.get(first.key) == first.payload


@pytest.mark.parametrize("component", ["runs-root", "cache", "features", "identity"])
def test_symlinked_path_component_refuses_and_cannot_write_outside(
    tmp_path: Path,
    component: str,
) -> None:
    case = _case()
    outside = tmp_path / "outside"
    outside.mkdir()
    if component == "runs-root":
        runs_root = tmp_path / "runs-link"
        runs_root.symlink_to(outside, target_is_directory=True)
    else:
        runs_root = tmp_path / "runs"
        runs_root.mkdir()
        parent = runs_root
        names = ["cache", "features", case.key]
        index = {"cache": 0, "features": 1, "identity": 2}[component]
        for name in names[:index]:
            parent /= name
            parent.mkdir()
        (parent / names[index]).symlink_to(outside, target_is_directory=True)

    with pytest.raises(TradingResearchError):
        ArtifactStore(runs_root).put(case.key, case.payload)
    assert tuple(outside.iterdir()) == ()


def test_partial_low_level_writes_are_completed_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    actual_write = store_module._write_fd
    calls = 0

    def short_write(descriptor: int, remaining: bytes) -> int:
        nonlocal calls
        calls += 1
        return actual_write(descriptor, remaining[:7])

    monkeypatch.setattr(store_module, "_write_fd", short_write)

    assert ArtifactStore(tmp_path).put(case.key, case.payload) == case.content_hash
    assert calls > 1
    assert ArtifactStore(tmp_path).get(case.key) == case.payload


def test_partial_write_failure_leaves_no_final_or_owned_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    actual_write = store_module._write_fd
    calls = 0

    def torn_then_failed(descriptor: int, remaining: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return actual_write(descriptor, remaining[:7])
        raise OSError("injected write failure")

    monkeypatch.setattr(store_module, "_write_fd", torn_then_failed)

    with pytest.raises(TradingResearchError):
        ArtifactStore(tmp_path).put(case.key, case.payload)
    assert _finals(tmp_path, case.key) == ()
    assert _temps(tmp_path, case.key) == ()


def test_temp_fsync_failure_leaves_no_final_or_owned_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    _identity_directory(tmp_path, case.key, create=True)
    actual_sync = store_module._sync_fd

    def fail_regular_file_sync(descriptor: int) -> None:
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("injected temp fsync failure")
        actual_sync(descriptor)

    monkeypatch.setattr(store_module, "_sync_fd", fail_regular_file_sync)

    with pytest.raises(TradingResearchError):
        ArtifactStore(tmp_path).put(case.key, case.payload)
    assert _finals(tmp_path, case.key) == ()
    assert _temps(tmp_path, case.key) == ()


def test_link_failure_after_fsync_leaves_no_final_or_owned_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()

    def failed_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected no-replace failure")

    monkeypatch.setattr(store_module, "_link_at", failed_link)

    with pytest.raises(TradingResearchError):
        ArtifactStore(tmp_path).put(case.key, case.payload)
    assert _finals(tmp_path, case.key) == ()
    assert _temps(tmp_path, case.key) == ()


def test_publication_has_exact_durability_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    directory = _identity_directory(tmp_path, case.key, create=True)
    identity_fd = os.open(directory, store_module._DIRECTORY_FLAGS)
    actual_write = store_module._write_fd
    actual_sync = store_module._sync_fd
    actual_close = store_module._close_fd
    actual_link = store_module._link_at
    actual_unlink = store_module._unlink_at
    events: list[str] = []

    def recording_write(descriptor: int, remaining: bytes) -> int:
        events.append("write")
        return actual_write(descriptor, remaining)

    def recording_sync(descriptor: int) -> None:
        events.append("dir-fsync" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "temp-fsync")
        actual_sync(descriptor)

    def recording_close(descriptor: int) -> None:
        events.append("temp-close")
        actual_close(descriptor)

    def recording_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
        follow_symlinks: bool,
    ) -> None:
        events.append("link")
        actual_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    def recording_unlink(name: str, *, dir_fd: int) -> None:
        events.append("unlink")
        actual_unlink(name, dir_fd=dir_fd)

    monkeypatch.setattr(store_module, "_write_fd", recording_write)
    monkeypatch.setattr(store_module, "_sync_fd", recording_sync)
    monkeypatch.setattr(store_module, "_close_fd", recording_close)
    monkeypatch.setattr(store_module, "_link_at", recording_link)
    monkeypatch.setattr(store_module, "_unlink_at", recording_unlink)
    try:
        store_module._publish_locked(
            identity_fd,
            identity_key=case.key,
            final_name=f"{case.content_hash}.npz",
            payload=case.payload,
        )
    finally:
        actual_close(identity_fd)

    assert events == ["write", "temp-fsync", "temp-close", "link", "dir-fsync", "unlink"]
    assert _finals(tmp_path, case.key)[0].read_bytes() == case.payload


def test_parent_fsync_failure_exposes_only_complete_final_and_retry_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    _identity_directory(tmp_path, case.key, create=True)
    actual_sync_directory = store_module._sync_directory

    def fail_publication_directory_sync(descriptor: int, *, label: str) -> None:
        if label.startswith("identity "):
            raise store_module._ArtifactPublicationError("injected parent fsync failure")
        actual_sync_directory(descriptor, label=label)

    monkeypatch.setattr(store_module, "_sync_directory", fail_publication_directory_sync)
    store = ArtifactStore(tmp_path)

    with pytest.raises(TradingResearchError):
        store.put(case.key, case.payload)
    final = _finals(tmp_path, case.key)
    assert len(final) == 1
    assert final[0].read_bytes() == case.payload
    assert _temps(tmp_path, case.key) == ()

    monkeypatch.setattr(store_module, "_sync_directory", actual_sync_directory)
    assert store.get(case.key) == case.payload
    assert store.put(case.key, case.payload) == case.content_hash


def test_cleanup_failure_after_new_link_is_best_effort_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    unlink_calls = 0

    def failed_unlink(_name: str, **_kwargs: object) -> None:
        nonlocal unlink_calls
        unlink_calls += 1
        raise OSError("injected cleanup refusal")

    monkeypatch.setattr(store_module, "_unlink_at", failed_unlink)
    store = ArtifactStore(tmp_path)

    assert store.put(case.key, case.payload) == case.content_hash
    assert unlink_calls == 2
    assert len(_finals(tmp_path, case.key)) == 1
    assert len(_temps(tmp_path, case.key)) == 1
    assert store.get(case.key) == case.payload


def test_directory_fsync_failure_leaves_no_final_and_retry_is_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    actual_sync_directory = store_module._sync_directory
    failed = False

    def fail_first_directory_sync(descriptor: int, *, label: str) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise store_module._ArtifactPublicationError("injected directory fsync failure")
        actual_sync_directory(descriptor, label=label)

    monkeypatch.setattr(store_module, "_sync_directory", fail_first_directory_sync)
    store = ArtifactStore(tmp_path)

    with pytest.raises(TradingResearchError):
        store.put(case.key, case.payload)
    assert _finals(tmp_path, case.key) == ()

    monkeypatch.setattr(store_module, "_sync_directory", actual_sync_directory)
    assert store.put(case.key, case.payload) == case.content_hash
    assert store.get(case.key) == case.payload


def test_identical_eexist_race_is_idempotent_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    actual_link = store_module._link_at

    def racing_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
        follow_symlinks: bool,
    ) -> None:
        actual_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )
        raise FileExistsError(destination)

    monkeypatch.setattr(store_module, "_link_at", racing_link)

    assert ArtifactStore(tmp_path).put(case.key, case.payload) == case.content_hash
    assert len(_finals(tmp_path, case.key)) == 1
    assert _temps(tmp_path, case.key) == ()


def test_identical_eexist_with_repeated_cleanup_failure_still_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    actual_link = store_module._link_at
    unlink_calls = 0

    def racing_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
        follow_symlinks: bool,
    ) -> None:
        actual_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )
        raise FileExistsError(destination)

    def failed_unlink(_name: str, **_kwargs: object) -> None:
        nonlocal unlink_calls
        unlink_calls += 1
        raise OSError("injected repeated cleanup refusal")

    monkeypatch.setattr(store_module, "_link_at", racing_link)
    monkeypatch.setattr(store_module, "_unlink_at", failed_unlink)
    store = ArtifactStore(tmp_path)

    assert store.put(case.key, case.payload) == case.content_hash
    assert unlink_calls == 2
    assert len(_finals(tmp_path, case.key)) == 1
    assert len(_temps(tmp_path, case.key)) == 1
    assert store.get(case.key) == case.payload


def test_eexist_with_different_bytes_is_a_loud_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()

    def racing_link(
        _source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
        follow_symlinks: bool,
    ) -> None:
        _ = (src_dir_fd, follow_symlinks)
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=dst_dir_fd,
        )
        try:
            os.write(descriptor, b"different")
        finally:
            os.close(descriptor)
        raise FileExistsError(destination)

    monkeypatch.setattr(store_module, "_link_at", racing_link)

    with pytest.raises(TradingResearchError):
        ArtifactStore(tmp_path).put(case.key, case.payload)
    assert len(_finals(tmp_path, case.key)) == 1
    assert _temps(tmp_path, case.key) == ()


def test_postpublication_corruption_is_detected_before_put_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    actual_link = store_module._link_at

    def corrupting_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
        follow_symlinks: bool,
    ) -> None:
        actual_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_TRUNC,
            dir_fd=dst_dir_fd,
        )
        try:
            os.write(descriptor, b"corrupt")
        finally:
            os.close(descriptor)

    monkeypatch.setattr(store_module, "_link_at", corrupting_link)

    with pytest.raises(TradingResearchError):
        ArtifactStore(tmp_path).put(case.key, case.payload)
    with pytest.raises(TradingResearchError):
        ArtifactStore(tmp_path).get(case.key)


def test_random_name_failure_is_wrapped_and_leaves_no_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    _identity_directory(tmp_path, case.key, create=True)

    def failed_entropy(_size: int) -> bytes:
        raise OSError("injected entropy failure")

    monkeypatch.setattr(store_module, "_random_bytes", failed_entropy)

    with pytest.raises(TradingResearchError):
        ArtifactStore(tmp_path).put(case.key, case.payload)
    assert _finals(tmp_path, case.key) == ()
    assert _temps(tmp_path, case.key) == ()


def test_temp_close_release_then_error_does_not_close_reused_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    actual_close = store_module._close_fd
    replacement_fd: int | None = None

    def release_reuse_then_fail(descriptor: int) -> None:
        nonlocal replacement_fd
        if replacement_fd is None and stat.S_ISREG(os.fstat(descriptor).st_mode):
            actual_close(descriptor)
            replacement_fd = os.open("/dev/null", os.O_RDONLY)
            assert replacement_fd == descriptor
            raise OSError("injected close error after descriptor release")
        actual_close(descriptor)

    monkeypatch.setattr(store_module, "_close_fd", release_reuse_then_fail)
    try:
        with pytest.raises(TradingResearchError):
            ArtifactStore(tmp_path).put(case.key, case.payload)

        assert replacement_fd is not None
        assert stat.S_ISCHR(os.fstat(replacement_fd).st_mode)
        assert _finals(tmp_path, case.key) == ()
    finally:
        if replacement_fd is not None:
            with contextlib.suppress(OSError):
                actual_close(replacement_fd)


def test_runs_root_traversal_closes_opened_child_when_parent_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_open = store_module._open_fd
    actual_close = store_module._close_fd
    opened: list[int] = []
    closed: list[int] = []
    failed = False

    def recording_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = actual_open(path, flags, mode, dir_fd=dir_fd)
        opened.append(descriptor)
        return descriptor

    def fail_first_close(descriptor: int) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("injected parent close failure")
        closed.append(descriptor)
        actual_close(descriptor)

    monkeypatch.setattr(store_module, "_open_fd", recording_open)
    monkeypatch.setattr(store_module, "_close_fd", fail_first_close)
    try:
        with pytest.raises(TradingResearchError):
            store_module._open_runs_root(tmp_path)
        assert len(opened) >= 2
        assert opened[1] in closed
    finally:
        for descriptor in set(opened) - set(closed):
            with contextlib.suppress(OSError):
                actual_close(descriptor)


def test_cache_traversal_closes_opened_child_when_parent_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case()
    (tmp_path / "cache").mkdir()
    actual_open = store_module._open_fd
    actual_close = store_module._close_fd
    root_fd = actual_open(tmp_path, store_module._DIRECTORY_FLAGS)
    opened: list[int] = [root_fd]
    closed: list[int] = []
    failed = False

    def pinned_root(_root: Path) -> int:
        return root_fd

    def recording_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = actual_open(path, flags, mode, dir_fd=dir_fd)
        opened.append(descriptor)
        return descriptor

    def fail_first_close(descriptor: int) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("injected cache-parent close failure")
        closed.append(descriptor)
        actual_close(descriptor)

    monkeypatch.setattr(store_module, "_open_runs_root", pinned_root)
    monkeypatch.setattr(store_module, "_open_fd", recording_open)
    monkeypatch.setattr(store_module, "_close_fd", fail_first_close)
    try:
        with pytest.raises(TradingResearchError):
            store_module._open_identity_directory(tmp_path, key=case.key, create=False)
        assert len(opened) == 2
        assert opened[1] in closed
    finally:
        for descriptor in set(opened) - set(closed):
            with contextlib.suppress(OSError):
                actual_close(descriptor)


_CHILD_PROGRAM = r"""
import base64
import json
import sys
import time
from pathlib import Path
from trading_research.artifacts import ArtifactStore

runs_root = Path(sys.argv[1])
identity_key = sys.argv[2]
payload = base64.b64decode(sys.argv[3], validate=True)
barrier = Path(sys.argv[4])
print("READY", flush=True)
while not barrier.exists():
    time.sleep(0.005)
try:
    content_hash = ArtifactStore(runs_root).put(identity_key, payload)
except BaseException as exc:
    print(json.dumps({"status": "error", "type": type(exc).__name__, "message": str(exc)}))
else:
    print(json.dumps({"status": "ok", "hash": content_hash}))
"""


def _start_publishers(
    *,
    root: Path,
    key: str,
    payloads: tuple[bytes, bytes],
) -> tuple[dict[str, str], dict[str, str]]:
    barrier = root.parent / f"{root.name}-go"
    working_directories = (root.parent / f"{root.name}-left", root.parent / f"{root.name}-right")
    for directory in working_directories:
        directory.mkdir()
    processes: list[subprocess.Popen[str]] = []
    for index, (payload, cwd) in enumerate(zip(payloads, working_directories, strict=True)):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = str(100 + index)
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _CHILD_PROGRAM,
                str(root),
                key,
                base64.b64encode(payload).decode("ascii"),
                str(barrier),
            ],
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append(process)
    for process in processes:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "READY"
    barrier.write_text("go\n")
    results: list[dict[str, str]] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr
        results.append(cast("dict[str, str]", json.loads(stdout.strip())))
    return results[0], results[1]


def test_two_real_subprocesses_publish_identical_bytes_idempotently(tmp_path: Path) -> None:
    case = _case()
    root = tmp_path / "runs-identical"
    root.mkdir()

    results = _start_publishers(
        root=root,
        key=case.key,
        payloads=(case.payload, case.payload),
    )

    assert results == (
        {"status": "ok", "hash": case.content_hash},
        {"status": "ok", "hash": case.content_hash},
    )
    assert len(_finals(root, case.key)) == 1
    assert _temps(root, case.key) == ()
    assert ArtifactStore(root).get(case.key) == case.payload


def test_two_real_subprocesses_with_different_bytes_conflict_loudly(tmp_path: Path) -> None:
    first = _case(first_value=1.0)
    second = _case(first_value=9.0)
    root = tmp_path / "runs-conflict"
    root.mkdir()

    results = _start_publishers(
        root=root,
        key=first.key,
        payloads=(first.payload, second.payload),
    )

    statuses = sorted(result["status"] for result in results)
    assert statuses == ["error", "ok"]
    winner = next(result for result in results if result["status"] == "ok")
    loser = next(result for result in results if result["status"] == "error")
    assert loser["type"] == "_ArtifactConflictError"
    expected_payload = (
        first.payload if winner["hash"] == first.content_hash else second.payload
    )
    assert winner["hash"] in {first.content_hash, second.content_hash}
    assert len(_finals(root, first.key)) == 1
    assert _temps(root, first.key) == ()
    assert ArtifactStore(root).get(first.key) == expected_payload
