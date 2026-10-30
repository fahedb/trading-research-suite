"""Slice 2E deterministic, self-describing feature artifact container."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import warnings
import zipfile
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pytest

from trading_research.artifacts import PlatformIdentity, StageIdentity
from trading_research.artifacts.container import (
    _decode_feature_artifact,
    _DecodedFeatureArtifact,
    _encode_feature_artifact,
)
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
    from collections.abc import Callable, Sequence
    from pathlib import Path

    import numpy.typing as npt

_DOMAIN = ClockDomain("artifact.wallclock")
_SOURCES = (
    DatasetIdentity("SYNTH_CONTEXT", "a" * 64),
    DatasetIdentity("SYNTH_EVALUATION", "b" * 64),
)
_COLUMN_NAMES = ("__meta__", "../odd/name")
_SPLIT_DIGEST = "c" * 64
_COMMIT = "d" * 40
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class _Provider:
    provider_id: str = "artifact_provider"
    version: int = 7
    feature_names: tuple[str, ...] = _COLUMN_NAMES
    required_columns: tuple[str, ...] = ("close",)
    window: WindowSpec = field(default_factory=lambda: WindowSpec(1, GapPolicy.CROSS))
    params: Mapping[str, ParamValue] = field(
        default_factory=lambda: {"flags": (True, "β", -0.0), "scale": 1.5}
    )
    cost_hint: CostHint = CostHint.CHEAP

    def compute(
        self,
        request: FeatureRequest,
    ) -> Mapping[str, npt.NDArray[np.float64]]:
        raise AssertionError(f"container fixture called provider compute with {request!r}")


def _key(
    value: int,
    *,
    unit: TimeUnit = TimeUnit.SECONDS,
    domain: ClockDomain = _DOMAIN,
) -> NativeTimeKey:
    return NativeTimeKey(value, unit, domain)


def _market_frame(
    keys: Sequence[int],
    *,
    source: DatasetIdentity,
    role: ViewRole,
    unit: TimeUnit = TimeUnit.SECONDS,
    domain: ClockDomain = _DOMAIN,
) -> MarketFrame:
    key_array = np.asarray(keys, dtype=np.int64)
    close = np.arange(100.0, 100.0 + key_array.size, dtype=np.float64)
    return _make_market_frame(
        dataset_ref=DatasetRef(source.dataset_id, source.manifest_sha256, role),
        clock_domain=domain,
        unit=unit,
        schema={"close": "float64"},
        keys=key_array,
        columns={"close": close},
        admitted_lo=int(key_array[0]),
        admitted_hi=int(key_array[-1]),
    )


def _request(
    *,
    sources: tuple[DatasetIdentity, DatasetIdentity] = _SOURCES,
    unit: TimeUnit = TimeUnit.SECONDS,
    domain: ClockDomain = _DOMAIN,
) -> FeatureRequest:
    frames = (
        _market_frame((10, 20), source=sources[0], role=ViewRole.TRAIN, unit=unit, domain=domain),
        _market_frame(
            (30, 40, 50),
            source=sources[1],
            role=ViewRole.VALIDATION,
            unit=unit,
            domain=domain,
        ),
    )
    return FeatureRequest.from_segments(
        frames,
        eval_start=_key(30, unit=unit, domain=domain),
        eval_end=_key(50, unit=unit, domain=domain),
        required_columns=("close",),
    )


def _column_values() -> OrderedDict[str, npt.NDArray[np.float64]]:
    exact_bits = np.asarray(
        [0x0000000000000000, 0x8000000000000000, 0x7FF8000000000042],
        dtype=np.uint64,
    ).view(np.float64)
    return OrderedDict(
        (
            (_COLUMN_NAMES[0], exact_bits.copy()),
            (_COLUMN_NAMES[1], np.asarray([1.25, -2.5, np.nan], dtype=np.float64)),
        )
    )


def _feature_frame(
    *,
    provider: _Provider | None = None,
    request: FeatureRequest | None = None,
) -> FeatureFrame:
    declared = _Provider() if provider is None else provider
    bounded = _request() if request is None else request
    origin = FeatureOrigin(
        provider_id=declared.provider_id,
        version=declared.version,
        params=declared.params,
        window=declared.window,
    )
    values = _column_values()
    return _make_feature_frame(
        request=bounded,
        columns=values,
        origins=OrderedDict((name, origin) for name in values),
    )


def _identity(
    *,
    provider: _Provider | None = None,
    sources: tuple[DatasetIdentity, ...] = _SOURCES,
    unit: TimeUnit = TimeUnit.SECONDS,
    domain: ClockDomain = _DOMAIN,
    eval_end: int = 50,
) -> StageIdentity:
    declared = _Provider() if provider is None else provider
    return StageIdentity(
        source_identities=sources,
        resolved_context_start=_key(10, unit=unit, domain=domain),
        eval_start=_key(30, unit=unit, domain=domain),
        eval_end=_key(eval_end, unit=unit, domain=domain),
        split_policy_adr="ADR-0012",
        split_policy_version="v1",
        split_policy_sha256=_SPLIT_DIGEST,
        providers=(declared,),
        platform_identity=PlatformIdentity(_COMMIT, False, None),
    )


def _independent_stage_document() -> dict[str, object]:
    return {
        "source_identities": [
            {"dataset_id": "SYNTH_CONTEXT", "manifest_sha256": "a" * 64},
            {"dataset_id": "SYNTH_EVALUATION", "manifest_sha256": "b" * 64},
        ],
        "bounds": {
            "resolved_context_start": 10,
            "eval_start": 30,
            "eval_end": 50,
            "time_unit": "seconds",
            "clock_domain": "artifact.wallclock",
        },
        "split_policy": {
            "adr": "ADR-0012",
            "policy_version": "v1",
            "policy_sha256": "c" * 64,
        },
        "providers": [
            {
                "provider_id": "artifact_provider",
                "version": 7,
                "params": {
                    "flags": [True, "β", {"__float_hex__": "-0x0.0p+0"}],
                    "scale": {"__float_hex__": "0x1.8000000000000p+0"},
                },
                "feature_names": [*_COLUMN_NAMES],
                "required_columns": ["close"],
                "window": {
                    "max_prior_rows": 1,
                    "gap_policy": "cross",
                    "nominal_interval": None,
                },
            }
        ],
        "platform": {
            "git_commit": "d" * 40,
            "git_dirty": False,
            "diff_sha256": None,
        },
    }


def _canonical_json_bytes(document: object) -> bytes:
    return json.dumps(
        document,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _thawed_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thawed_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_thawed_json(item) for item in value]
    return value


def _independent_meta() -> dict[str, object]:
    stage_document = _independent_stage_document()
    identity_key = hashlib.sha256(_canonical_json_bytes(stage_document)).hexdigest()
    return {
        "format": "trading-research-feature-frame",
        "format_version": 1,
        "identity_key": identity_key,
        "stage_identity": stage_document,
        "row_count": 3,
        "columns": [
            {"name": name, "member": f"__column_{index}", "dtype": "float64"}
            for index, name in enumerate(_COLUMN_NAMES)
        ],
    }


def _zip_entries(payload: bytes) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(payload), mode="r") as archive:
        return [(info.filename, archive.read(info)) for info in archive.infolist()]


def _pack_zip(
    entries: Sequence[tuple[str, bytes]],
    *,
    compressed_names: frozenset[str] = frozenset(),
    timestamp: tuple[int, int, int, int, int, int] = _ZIP_TIMESTAMP,
) -> bytes:
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, mode="w") as archive:
            for name, data in entries:
                info = zipfile.ZipInfo(name, date_time=timestamp)
                info.compress_type = (
                    zipfile.ZIP_DEFLATED if name in compressed_names else zipfile.ZIP_STORED
                )
                archive.writestr(info, data)
    return output.getvalue()


def _npy_bytes(array: npt.NDArray[Any]) -> bytes:
    output = io.BytesIO()
    np.save(output, array, allow_pickle=array.dtype == np.dtype(object))
    return output.getvalue()


def _replace_member(payload: bytes, name: str, data: bytes) -> bytes:
    entries = _zip_entries(payload)
    assert sum(entry_name == name for entry_name, _ in entries) == 1
    return _pack_zip(
        [(entry_name, data if entry_name == name else entry) for entry_name, entry in entries]
    )


def _meta_array(payload: bytes) -> npt.NDArray[np.uint8]:
    member = dict(_zip_entries(payload))["__meta__.npy"]
    loaded = np.load(io.BytesIO(member), allow_pickle=False)
    assert type(loaded) is np.ndarray
    return cast("npt.NDArray[np.uint8]", loaded)


def _meta_document(payload: bytes) -> dict[str, object]:
    raw = _meta_array(payload).tobytes()
    return cast("dict[str, object]", json.loads(raw.decode("utf-8")))


def _with_meta_array(payload: bytes, array: npt.NDArray[np.generic]) -> bytes:
    return _replace_member(payload, "__meta__.npy", _npy_bytes(array))


def _with_meta_document(
    payload: bytes,
    mutate: Callable[[dict[str, object]], None],
    *,
    canonical: bool = True,
) -> bytes:
    document = _meta_document(payload)
    mutate(document)
    encoded = (
        _canonical_json_bytes(document)
        if canonical
        else json.dumps(document, ensure_ascii=False, indent=1).encode("utf-8")
    )
    return _with_meta_array(payload, np.frombuffer(encoded, dtype=np.uint8).copy())


def _assert_bits_equal(
    actual: npt.NDArray[np.float64],
    expected: npt.NDArray[np.float64],
) -> None:
    assert actual.dtype == np.dtype(np.float64)
    assert actual.dtype.metadata is None
    assert actual.view(np.uint64).tobytes() == expected.view(np.uint64).tobytes()


def _assert_round_trip(
    decoded: _DecodedFeatureArtifact,
    *,
    identity: StageIdentity,
    frame: FeatureFrame,
) -> None:
    assert decoded._identity_key == identity.identity_key()
    assert _thawed_json(decoded._stage_document) == _independent_stage_document()
    assert decoded._unit is frame.unit
    assert decoded._clock_domain == frame.clock_domain
    assert decoded._source_identities == frame.source_identities
    assert decoded._column_names == frame.column_names
    keys = decoded._keys._descriptor()
    assert keys.dtype == np.dtype(np.int64)
    assert keys.dtype.metadata is None
    assert keys.tobytes() == frame.keys.tobytes()
    assert tuple(decoded._columns) == frame.column_names
    assert tuple(decoded._origins) == frame.column_names
    for name in frame.column_names:
        _assert_bits_equal(decoded._columns[name]._descriptor(), frame.column(name))
        assert decoded._origins[name] == frame.origin(name)


def test_canonical_meta_is_independently_reconstructed_and_exact() -> None:
    identity = _identity()
    payload = _encode_feature_artifact(identity, _feature_frame())
    expected_meta = _independent_meta()
    expected_bytes = _canonical_json_bytes(expected_meta)
    meta = _meta_array(payload)

    assert identity.identity_key() == expected_meta["identity_key"]
    assert meta.ndim == 1
    assert meta.dtype == np.dtype(np.uint8)
    assert meta.dtype.metadata is None
    assert meta.tobytes() == expected_bytes
    assert meta.tobytes().isascii()


def test_round_trip_preserves_every_field_and_float64_bit() -> None:
    identity = _identity()
    frame = _feature_frame()
    payload = _encode_feature_artifact(identity, frame)
    decoded = _decode_feature_artifact(
        payload,
        expected_identity_key=identity.identity_key(),
    )

    _assert_round_trip(decoded, identity=identity, frame=frame)
    first = decoded._columns[_COLUMN_NAMES[0]]._descriptor().view(np.uint64)
    assert first.tolist() == [
        0x0000000000000000,
        0x8000000000000000,
        0x7FF8000000000042,
    ]


def test_zip_layout_is_fixed_uncompressed_timestamped_and_path_safe() -> None:
    payload = _encode_feature_artifact(_identity(), _feature_frame())
    with zipfile.ZipFile(io.BytesIO(payload), mode="r") as archive:
        infos = archive.infolist()

    assert [info.filename for info in infos] == [
        "__meta__.npy",
        "__keys__.npy",
        "__column_0.npy",
        "__column_1.npy",
    ]
    assert len({info.filename for info in infos}) == len(infos)
    assert all(info.compress_type == zipfile.ZIP_STORED for info in infos)
    assert all(info.date_time == _ZIP_TIMESTAMP for info in infos)
    assert all(
        "/" not in info.filename
        and "\\" not in info.filename
        and info.filename not in {".", ".."}
        for info in infos
    )
    assert all(info.filename.endswith(".npy") for info in infos)
    assert archive.comment == b""


def test_repeat_encoding_is_byte_identical() -> None:
    identity = _identity()
    frame = _feature_frame()
    first = _encode_feature_artifact(identity, frame)
    second = _encode_feature_artifact(identity, frame)

    assert first == second
    assert hashlib.sha256(first).digest() == hashlib.sha256(second).digest()


_CHILD_PROGRAM = r"""
import base64
from collections import OrderedDict
import numpy as np
from trading_research.artifacts import PlatformIdentity, StageIdentity
from trading_research.artifacts.container import _encode_feature_artifact
from trading_research.core import (
    ClockDomain, CostHint, DatasetIdentity, DatasetRef, FeatureOrigin, FeatureRequest,
    GapPolicy, NativeTimeKey, TimeUnit, ViewRole, WindowSpec,
)
from trading_research.core.feature_frames import _make_feature_frame
from trading_research.core.frames import _make_market_frame

domain = ClockDomain("artifact.wallclock")
sources = (
    DatasetIdentity("SYNTH_CONTEXT", "a" * 64),
    DatasetIdentity("SYNTH_EVALUATION", "b" * 64),
)
names = ("__meta__", "../odd/name")

class Provider:
    provider_id = "artifact_provider"
    version = 7
    feature_names = names
    required_columns = ("close",)
    window = WindowSpec(1, GapPolicy.CROSS)
    params = {"scale": 1.5, "flags": (True, "β", -0.0)}
    cost_hint = CostHint.CHEAP
    def compute(self, request):
        raise AssertionError(request)

def key(value):
    return NativeTimeKey(value, TimeUnit.SECONDS, domain)

def market(keys, source, role):
    key_array = np.asarray(keys, dtype=np.int64)
    close = np.arange(100.0, 100.0 + key_array.size, dtype=np.float64)
    return _make_market_frame(
        dataset_ref=DatasetRef(source.dataset_id, source.manifest_sha256, role),
        clock_domain=domain, unit=TimeUnit.SECONDS, schema={"close": "float64"},
        keys=key_array, columns={"close": close}, admitted_lo=int(key_array[0]),
        admitted_hi=int(key_array[-1]),
    )

request = FeatureRequest.from_segments(
    (market((10, 20), sources[0], ViewRole.TRAIN),
     market((30, 40, 50), sources[1], ViewRole.VALIDATION)),
    eval_start=key(30), eval_end=key(50), required_columns=("close",),
)
provider = Provider()
origin = FeatureOrigin(
    provider.provider_id, provider.version, provider.params, provider.window,
)
values = OrderedDict((
    (names[0], np.asarray(
        [0x0000000000000000, 0x8000000000000000, 0x7FF8000000000042],
        dtype=np.uint64,
    ).view(np.float64).copy()),
    (names[1], np.asarray([1.25, -2.5, np.nan], dtype=np.float64)),
))
frame = _make_feature_frame(
    request=request, columns=values,
    origins=OrderedDict((name, origin) for name in values),
)
identity = StageIdentity(
    source_identities=sources, resolved_context_start=key(10), eval_start=key(30),
    eval_end=key(50), split_policy_adr="ADR-0012", split_policy_version="v1",
    split_policy_sha256="c" * 64, providers=(provider,),
    platform_identity=PlatformIdentity("d" * 40, False, None),
)
print(base64.b64encode(_encode_feature_artifact(identity, frame)).decode("ascii"))
"""


def _subprocess_payload(*, cwd: str, hash_seed: str) -> bytes:
    environment = os.environ.copy()
    environment["PYTHONHASHSEED"] = hash_seed
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_PROGRAM],
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return base64.b64decode(completed.stdout.strip(), validate=True)


def test_fresh_process_and_hash_seed_do_not_change_bytes(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    local = _encode_feature_artifact(_identity(), _feature_frame())

    first = _subprocess_payload(cwd=str(left), hash_seed="1")
    second = _subprocess_payload(cwd=str(right), hash_seed="987654")

    assert first == local
    assert second == local


@pytest.mark.parametrize(
    "identity",
    [
        _identity(
            sources=(
                DatasetIdentity("OTHER_CONTEXT", "a" * 64),
                _SOURCES[1],
            )
        ),
        _identity(eval_end=40),
        _identity(provider=_Provider(feature_names=("other", "../odd/name"))),
        _identity(provider=_Provider(version=8)),
        _identity(unit=TimeUnit.MILLISECONDS),
    ],
    ids=["sources", "evaluation-bound", "column-declaration", "origin", "time-unit"],
)
def test_encoder_refuses_stage_identity_that_does_not_describe_frame(
    identity: StageIdentity,
) -> None:
    with pytest.raises(TradingResearchError):
        _encode_feature_artifact(identity, _feature_frame())


def test_decoder_refuses_expected_identity_mismatch() -> None:
    payload = _encode_feature_artifact(_identity(), _feature_frame())

    with pytest.raises(TradingResearchError):
        _decode_feature_artifact(payload, expected_identity_key="f" * 64)


def _duplicate_member(payload: bytes) -> bytes:
    entries = _zip_entries(payload)
    return _pack_zip((*entries, entries[-1]))


def _extra_member(payload: bytes) -> bytes:
    extra = _npy_bytes(np.asarray([1.0], dtype=np.float64))
    return _pack_zip((*_zip_entries(payload), ("extra.npy", extra)))


def _reordered_members(payload: bytes) -> bytes:
    entries = _zip_entries(payload)
    return _pack_zip((entries[1], entries[0], *entries[2:]))


def _compressed_member(payload: bytes) -> bytes:
    entries = _zip_entries(payload)
    return _pack_zip(entries, compressed_names=frozenset({"__column_0.npy"}))


def _object_member(payload: bytes) -> bytes:
    array = np.asarray(["zero", "minus-zero", "nan"], dtype=object)
    return _replace_member(payload, "__column_0.npy", _npy_bytes(array))


def _missing_meta_field(payload: bytes) -> bytes:
    def mutate(document: dict[str, object]) -> None:
        document.pop("row_count")

    return _with_meta_document(payload, mutate)


def _extra_meta_field(payload: bytes) -> bytes:
    def mutate(document: dict[str, object]) -> None:
        document["surprise"] = True

    return _with_meta_document(payload, mutate)


def _noncanonical_meta(payload: bytes) -> bytes:
    return _with_meta_document(payload, lambda _document: None, canonical=False)


def _invalid_json_meta(payload: bytes) -> bytes:
    return _with_meta_array(payload, np.frombuffer(b"{", dtype=np.uint8).copy())


def _invalid_utf8_meta(payload: bytes) -> bytes:
    return _with_meta_array(payload, np.asarray([0xFF], dtype=np.uint8))


def _wrong_meta_dtype(payload: bytes) -> bytes:
    return _with_meta_array(payload, np.asarray([1, 2, 3], dtype=np.int64))


def _wrong_meta_shape(payload: bytes) -> bytes:
    meta = _meta_array(payload).reshape(1, -1)
    return _with_meta_array(payload, meta)


def _wrong_keys_dtype(payload: bytes) -> bytes:
    return _replace_member(
        payload,
        "__keys__.npy",
        _npy_bytes(np.asarray([30.0, 40.0, 50.0], dtype=np.float64)),
    )


def _wrong_keys_shape(payload: bytes) -> bytes:
    return _replace_member(
        payload,
        "__keys__.npy",
        _npy_bytes(np.asarray([[30, 40, 50]], dtype=np.int64)),
    )


def _unordered_keys(payload: bytes) -> bytes:
    return _replace_member(
        payload,
        "__keys__.npy",
        _npy_bytes(np.asarray([30, 30, 50], dtype=np.int64)),
    )


def _wrong_column_dtype(payload: bytes) -> bytes:
    return _replace_member(
        payload,
        "__column_0.npy",
        _npy_bytes(np.asarray([0, 1, 2], dtype=np.int64)),
    )


def _wrong_column_shape(payload: bytes) -> bytes:
    return _replace_member(
        payload,
        "__column_0.npy",
        _npy_bytes(np.asarray([[0.0], [1.0], [2.0]], dtype=np.float64)),
    )


def _wrong_column_length(payload: bytes) -> bytes:
    return _replace_member(
        payload,
        "__column_0.npy",
        _npy_bytes(np.asarray([0.0, 1.0], dtype=np.float64)),
    )


def _infinite_column(payload: bytes) -> bytes:
    return _replace_member(
        payload,
        "__column_0.npy",
        _npy_bytes(np.asarray([0.0, np.inf, 2.0], dtype=np.float64)),
    )


def _wrong_row_count(payload: bytes) -> bytes:
    def mutate(document: dict[str, object]) -> None:
        document["row_count"] = 4

    return _with_meta_document(payload, mutate)


def _wrong_declared_member(payload: bytes) -> bytes:
    def mutate(document: dict[str, object]) -> None:
        columns = cast("list[dict[str, object]]", document["columns"])
        columns[0]["member"] = "__column_9"

    return _with_meta_document(payload, mutate)


def _stage_document_tamper(payload: bytes) -> bytes:
    def mutate(document: dict[str, object]) -> None:
        stage = cast("dict[str, object]", document["stage_identity"])
        platform = cast("dict[str, object]", stage["platform"])
        platform["git_commit"] = "e" * 40

    return _with_meta_document(payload, mutate)


_CORRUPTIONS: tuple[tuple[str, Callable[[bytes], bytes]], ...] = (
    ("truncated", lambda payload: payload[: len(payload) // 2]),
    ("wrong-magic", lambda _payload: b"not-an-npz"),
    ("duplicate-member", _duplicate_member),
    ("extra-member", _extra_member),
    ("reordered-members", _reordered_members),
    ("compressed-member", _compressed_member),
    ("object-member", _object_member),
    ("missing-meta-field", _missing_meta_field),
    ("extra-meta-field", _extra_meta_field),
    ("noncanonical-meta", _noncanonical_meta),
    ("invalid-json-meta", _invalid_json_meta),
    ("invalid-utf8-meta", _invalid_utf8_meta),
    ("wrong-meta-dtype", _wrong_meta_dtype),
    ("wrong-meta-shape", _wrong_meta_shape),
    ("wrong-keys-dtype", _wrong_keys_dtype),
    ("wrong-keys-shape", _wrong_keys_shape),
    ("unordered-keys", _unordered_keys),
    ("wrong-column-dtype", _wrong_column_dtype),
    ("wrong-column-shape", _wrong_column_shape),
    ("wrong-column-length", _wrong_column_length),
    ("infinite-column", _infinite_column),
    ("wrong-row-count", _wrong_row_count),
    ("wrong-declared-member", _wrong_declared_member),
    ("stage-document-tamper", _stage_document_tamper),
)


@pytest.mark.parametrize(
    "corrupt",
    [case[1] for case in _CORRUPTIONS],
    ids=[case[0] for case in _CORRUPTIONS],
)
def test_decoder_refuses_every_noncanonical_or_malformed_container(
    corrupt: Callable[[bytes], bytes],
) -> None:
    identity = _identity()
    payload = _encode_feature_artifact(identity, _feature_frame())

    with pytest.raises(TradingResearchError):
        _decode_feature_artifact(
            corrupt(payload),
            expected_identity_key=identity.identity_key(),
        )


def test_object_array_is_eagerly_refused_with_pickle_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity()
    payload = _object_member(_encode_feature_artifact(identity, _feature_frame()))
    actual_load = cast("Callable[..., object]", np.load)
    observed_allow_pickle: list[object] = []

    def recording_load(*args: object, **kwargs: object) -> object:
        observed_allow_pickle.append(kwargs.get("allow_pickle"))
        return actual_load(*args, **kwargs)

    monkeypatch.setattr(np, "load", recording_load)

    with pytest.raises(TradingResearchError):
        _decode_feature_artifact(payload, expected_identity_key=identity.identity_key())

    assert observed_allow_pickle
    assert set(observed_allow_pickle) == {False}


def test_decodes_are_read_only_and_detached_from_each_other_and_payload() -> None:
    identity = _identity()
    payload = _encode_feature_artifact(identity, _feature_frame())
    before = bytes(payload)
    first = _decode_feature_artifact(payload, expected_identity_key=identity.identity_key())
    second = _decode_feature_artifact(payload, expected_identity_key=identity.identity_key())

    assert first is not second
    assert first._stage_document is not second._stage_document
    assert first._keys is not second._keys
    first_keys = first._keys._descriptor()
    second_keys = second._keys._descriptor()
    assert not np.shares_memory(first_keys, second_keys)
    assert first_keys.flags.writeable is False
    for name in first._column_names:
        assert first._columns[name] is not second._columns[name]
        first_values = first._columns[name]._descriptor()
        second_values = second._columns[name]._descriptor()
        assert not np.shares_memory(first_values, second_values)
        assert first_values.flags.writeable is False
        _assert_bits_equal(first_values, second_values)
    assert payload == before
