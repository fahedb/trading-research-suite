"""Fail-closed worker environment, admission arithmetic, and disk telemetry."""

from __future__ import annotations

import math as _math
import os as _os
import resource as _resource
import signal as _signal
import typing as _typing
from dataclasses import dataclass as _dataclass
from pathlib import Path as _Path

from trading_research.core.errors import ContractViolationError as _ContractViolationError

if _typing.TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

__all__: list[str] = []

_THREAD_CLAMPS = {
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
}
_DEFAULT_MAX_WORKERS = 8
_DEFAULT_ASSUMED_PEAK_RSS_BYTES = 1_073_741_824
_DEFAULT_MEMORY_BUDGET_BYTES = 17_179_869_184
_MEMORY_BUDGET_TOTAL_FRACTION_NUMERATOR = 4
_MEMORY_BUDGET_TOTAL_FRACTION_DENOMINATOR = 5
_DISK_WARNING_BASIS_POINTS = 7_000
_DISK_REFUSAL_BASIS_POINTS = 9_000


def _apply_worker_limits(runtime_seconds: int | None, memory_bytes: int | None) -> None:
    """Apply optional POSIX limits in this command/worker process only."""
    for value in (runtime_seconds, memory_bytes):
        if value is not None and (type(value) is not int or value <= 0):
            raise _ContractViolationError("worker limits must be positive integers")
    if memory_bytes is not None:
        _resource.setrlimit(_resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    if runtime_seconds is not None:

        def expired(_signum: int, _frame: object) -> None:
            raise TimeoutError("experiment exceeded its wall/CPU time budget")

        _signal.signal(_signal.SIGALRM, expired)
        _signal.signal(_signal.SIGXCPU, expired)
        _signal.alarm(runtime_seconds)
        usage = _resource.getrusage(_resource.RUSAGE_SELF)
        cpu_limit = _math.ceil(usage.ru_utime + usage.ru_stime) + runtime_seconds
        _resource.setrlimit(_resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))


@_dataclass(frozen=True, slots=True)
class _DiskUsage:
    _total_bytes: int
    _used_bytes: int
    _available_bytes: int
    _used_basis_points: int
    _warning: bool
    _refusal: bool


def _worker_environment(base: _Mapping[str, str] | None = None) -> dict[str, str]:
    """Return one explicit child environment with every BLAS clamp applied."""
    source = _os.environ if base is None else base
    if not isinstance(source, _typing.Mapping):
        raise _ContractViolationError("worker environment base must be a mapping")
    environment: dict[str, str] = {}
    for key, value in source.items():
        if type(key) is not str or type(value) is not str or not key or "\x00" in key + value:
            raise _ContractViolationError("worker environment contains a malformed entry")
        environment[key] = value
    environment.update(_THREAD_CLAMPS)
    return environment


def _assert_worker_environment(environment: _Mapping[str, str] | None = None) -> None:
    """Refuse execution unless all import-time thread controls are exact."""
    observed = _os.environ if environment is None else environment
    if not isinstance(observed, _typing.Mapping):
        raise _ContractViolationError("worker environment must be a mapping")
    mismatches = tuple(
        name for name, expected in _THREAD_CLAMPS.items() if observed.get(name) != expected
    )
    if mismatches:
        raise _ContractViolationError(
            "worker thread clamps are missing or invalid: " + ", ".join(sorted(mismatches))
        )


def _effective_worker_count(
    *,
    requested_workers: int = _DEFAULT_MAX_WORKERS,
    memory_budget_bytes: int = _DEFAULT_MEMORY_BUDGET_BYTES,
    assumed_peak_rss_bytes: int = _DEFAULT_ASSUMED_PEAK_RSS_BYTES,
    cpu_count: int | None = None,
    total_memory_bytes: int | None = None,
) -> int:
    """Compute the exact CPU-and-memory admission cap from declared inputs."""
    for value, label in (
        (requested_workers, "requested_workers"),
        (memory_budget_bytes, "memory_budget_bytes"),
        (assumed_peak_rss_bytes, "assumed_peak_rss_bytes"),
    ):
        if type(value) is not int or value <= 0:
            raise _ContractViolationError(f"{label} must be an exact positive int")
    detected_cpus = _os.cpu_count() if cpu_count is None else cpu_count
    if type(detected_cpus) is not int or detected_cpus <= 0:
        raise _ContractViolationError("cpu_count must be an exact positive int")
    detected_memory = (
        _detected_total_memory_bytes() if total_memory_bytes is None else total_memory_bytes
    )
    if type(detected_memory) is not int or detected_memory <= 0:
        raise _ContractViolationError("total_memory_bytes must be an exact positive int")
    maximum_budget = (
        detected_memory
        * _MEMORY_BUDGET_TOTAL_FRACTION_NUMERATOR
        // _MEMORY_BUDGET_TOTAL_FRACTION_DENOMINATOR
    )
    if memory_budget_bytes > maximum_budget:
        raise _ContractViolationError(
            "memory_budget_bytes exceeds the 80 percent detected-memory ceiling"
        )
    memory_workers = max(1, memory_budget_bytes // assumed_peak_rss_bytes)
    cpu_workers = max(1, detected_cpus - 1)
    return min(requested_workers, memory_workers, cpu_workers)


def _detected_total_memory_bytes() -> int:
    try:
        page_size = _os.sysconf("SC_PAGE_SIZE")
        page_count = _os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError) as exc:
        raise _ContractViolationError("cannot detect total system memory") from exc
    total = page_size * page_count
    if type(total) is not int or total <= 0:
        raise _ContractViolationError("detected total memory is invalid")
    return total


def _disk_usage(path: _Path) -> _DiskUsage:
    if not isinstance(path, _Path) or not path.is_absolute():
        raise _ContractViolationError("disk telemetry path must be absolute")
    try:
        statistics = _os.statvfs(path)
    except OSError as exc:
        raise _ContractViolationError("cannot read filesystem capacity") from exc
    total = statistics.f_blocks * statistics.f_frsize
    available = statistics.f_bavail * statistics.f_frsize
    if total <= 0 or available < 0 or available > total:
        raise _ContractViolationError("filesystem capacity values are invalid")
    used = total - available
    basis_points = used * 10_000 // total
    return _DiskUsage(
        total,
        used,
        available,
        basis_points,
        basis_points >= _DISK_WARNING_BASIS_POINTS,
        basis_points >= _DISK_REFUSAL_BASIS_POINTS,
    )


def _enforce_disk_preflight(path: _Path, *, allow_critical_usage: bool) -> _DiskUsage:
    if type(allow_critical_usage) is not bool:
        raise _ContractViolationError("allow_critical_usage must be an exact bool")
    usage = _disk_usage(path)
    if usage._refusal and not allow_critical_usage:
        raise _ContractViolationError(
            "runs filesystem is at or above 90 percent used; explicit override required"
        )
    return usage
