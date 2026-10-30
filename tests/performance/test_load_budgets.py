"""Explicit published-TRAIN performance qualification targets."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.performance._measure_child import (
    DATASET_ROOT,
    POLICY_PATH,
    RAW_MANIFEST,
    RSS_BUDGET_BYTES,
    TRAIN_EXPECTED_ROWS,
    TRAIN_FULL_COLUMNS,
    TRAIN_FULL_CONFIGURATION,
    TRAIN_MANIFEST,
    TRAIN_TWO_COLUMN_CONFIGURATION,
    TRAIN_TWO_COLUMNS,
    VIEWS_ROOT,
    WARM_BUDGET_SECONDS,
    WARM_REPETITIONS,
    _prepare_train_access,
    _summarize_samples,
    _timed_load,
)

pytestmark = pytest.mark.performance

_REPO_ROOT = Path(__file__).parents[2]
_CHILD_OUTPUT_KEYS = {
    "elapsed_seconds",
    "pre_load_rss_bytes",
    "peak_rss_bytes",
    "retained_payload_bytes",
    "row_count",
    "column_names",
}


@dataclass(frozen=True, slots=True)
class _ChildMeasurement:
    elapsed_seconds: float
    pre_load_rss_bytes: int
    peak_rss_bytes: int
    retained_payload_bytes: int
    row_count: int
    column_names: tuple[str, ...]


@pytest.fixture(scope="module", autouse=True)
def _require_published_train() -> None:
    """Skip without resolving a registry when the research VM data is absent."""
    if sys.platform != "linux":
        pytest.skip("Phase 1 ru_maxrss qualification requires Linux")
    directories = (DATASET_ROOT, VIEWS_ROOT)
    files = (POLICY_PATH, RAW_MANIFEST, TRAIN_MANIFEST)
    if not all(path.is_dir() for path in directories) or not all(
        path.is_file() for path in files
    ):
        pytest.skip("published TRAIN research dataset/views are unavailable")


def _parse_child_output(stdout: str) -> _ChildMeasurement:
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in fields:
            raise AssertionError(f"invalid measurement-child output line: {line!r}")
        fields[key] = value
    if set(fields) != _CHILD_OUTPUT_KEYS:
        raise AssertionError(
            f"measurement-child output keys differ: observed {sorted(fields)}"
        )
    column_names = tuple(fields["column_names"].split(","))
    measurement = _ChildMeasurement(
        elapsed_seconds=float(fields["elapsed_seconds"]),
        pre_load_rss_bytes=int(fields["pre_load_rss_bytes"]),
        peak_rss_bytes=int(fields["peak_rss_bytes"]),
        retained_payload_bytes=int(fields["retained_payload_bytes"]),
        row_count=int(fields["row_count"]),
        column_names=column_names,
    )
    if (
        measurement.elapsed_seconds < 0.0
        or measurement.pre_load_rss_bytes <= 0
        or measurement.peak_rss_bytes < measurement.pre_load_rss_bytes
        or measurement.retained_payload_bytes <= 0
    ):
        raise AssertionError(f"invalid operational measurement: {measurement!r}")
    return measurement


def _run_measurement_child(configuration: str) -> _ChildMeasurement:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.performance._measure_child", configuration],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return _parse_child_output(completed.stdout)


@pytest.fixture(scope="module")
def rss_measurements() -> dict[str, _ChildMeasurement]:
    """Use exactly one fresh process for each approved TRAIN configuration."""
    return {
        configuration: _run_measurement_child(configuration)
        for configuration in (
            TRAIN_FULL_CONFIGURATION,
            TRAIN_TWO_COLUMN_CONFIGURATION,
        )
    }


def test_warm_published_train_load_is_within_budget(
    capsys: pytest.CaptureFixture[str],
) -> None:
    grant = _prepare_train_access()
    assert grant.classification.name == "NORMAL"
    assert grant.audit_event_id is None

    warmup, _ = _timed_load(grant, columns=None)
    assert warmup.num_rows == TRAIN_EXPECTED_ROWS
    assert warmup.column_names == TRAIN_FULL_COLUMNS
    del warmup

    samples: list[float] = []
    for _ in range(WARM_REPETITIONS):
        frame, elapsed_seconds = _timed_load(grant, columns=None)
        samples.append(elapsed_seconds)
        assert frame.num_rows == TRAIN_EXPECTED_ROWS
        assert frame.column_names == TRAIN_FULL_COLUMNS
        del frame

    summary = _summarize_samples(samples)
    with capsys.disabled():
        print(
            "phase1_train_warm_seconds "
            f"median={summary.median_seconds:.6f} "
            f"min={summary.minimum_seconds:.6f} repetitions={WARM_REPETITIONS}"
        )
    assert summary.median_seconds < WARM_BUDGET_SECONDS


def test_published_train_full_columns_rss_is_within_budget(
    rss_measurements: dict[str, _ChildMeasurement],
    capsys: pytest.CaptureFixture[str],
) -> None:
    measurement = rss_measurements[TRAIN_FULL_CONFIGURATION]
    with capsys.disabled():
        print(
            "phase1_train_full "
            f"elapsed_seconds={measurement.elapsed_seconds:.6f} "
            f"pre_load_rss_bytes={measurement.pre_load_rss_bytes} "
            f"peak_rss_bytes={measurement.peak_rss_bytes} "
            f"retained_payload_bytes={measurement.retained_payload_bytes} "
            f"rows={measurement.row_count} "
            f"columns={','.join(measurement.column_names)}"
        )
    assert measurement.row_count == TRAIN_EXPECTED_ROWS
    assert measurement.column_names == TRAIN_FULL_COLUMNS
    assert measurement.peak_rss_bytes < RSS_BUDGET_BYTES


def test_published_train_two_column_rss_is_within_budget_and_payload_is_lower(
    rss_measurements: dict[str, _ChildMeasurement],
    capsys: pytest.CaptureFixture[str],
) -> None:
    full = rss_measurements[TRAIN_FULL_CONFIGURATION]
    selected = rss_measurements[TRAIN_TWO_COLUMN_CONFIGURATION]
    with capsys.disabled():
        print(
            "phase1_train_two_column "
            f"elapsed_seconds={selected.elapsed_seconds:.6f} "
            f"pre_load_rss_bytes={selected.pre_load_rss_bytes} "
            f"peak_rss_bytes={selected.peak_rss_bytes} "
            f"retained_payload_bytes={selected.retained_payload_bytes} "
            f"rows={selected.row_count} "
            f"columns={','.join(selected.column_names)}"
        )
    assert selected.row_count == TRAIN_EXPECTED_ROWS
    assert selected.column_names == TRAIN_TWO_COLUMNS
    assert selected.peak_rss_bytes < RSS_BUDGET_BYTES
    assert selected.retained_payload_bytes < full.retained_payload_bytes
