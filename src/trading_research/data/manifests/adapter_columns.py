"""Private dtype declarations used when generating strict view manifests.

Legacy source manifests declare column names and purposes but do not carry
the dtype strings required by the v1.1 wire schema.  This module supplies
only that missing serialization metadata.  The physical adapter remains the
sole decoder and runtime type-enforcement authority; a conformance test pins
this declaration to its verified contract.
"""

from __future__ import annotations

from types import MappingProxyType as _MappingProxyType
from typing import TYPE_CHECKING as _TYPE_CHECKING

if _TYPE_CHECKING:
    from collections.abc import Mapping as _Mapping

__all__: list[str] = []

_MT5_EXPORTER_V1_DTYPES: _Mapping[str, str] = _MappingProxyType(
    {
        "mt5_time_raw": "int64",
        "timestamp_mt5_encoded": "datetime64[ns]",
        "open": "float64",
        "high": "float64",
        "low": "float64",
        "close": "float64",
        "tick_volume": "int64",
        "spread": "int32",
        "real_volume": "int64",
    }
)

_GENERATION_DTYPES_BY_ADAPTER: _Mapping[str, _Mapping[str, str]] = (
    _MappingProxyType({"mt5": _MT5_EXPORTER_V1_DTYPES})
)
