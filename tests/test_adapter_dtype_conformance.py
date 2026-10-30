"""Conformance between manifest-generation dtypes and adapter enforcement."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, cast

import pytest

from tests.manifest_fixtures import v11_manifest, write_manifest
from trading_research.core.errors import PartitionReadError
from trading_research.data.manifests import read_manifest

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

adapter_columns = cast(
    "Any",
    importlib.import_module("trading_research.data.manifests.adapter_columns"),
)
mt5 = cast("Any", importlib.import_module("trading_research.data.adapters.mt5"))


def _require_adapter_conformance(
    tmp_path: Path,
    declaration: Mapping[str, str],
) -> None:
    """Require exact inventory agreement and exercise adapter enforcement."""
    enforced_inventory = tuple(column.name for column in mt5._EXPORTER_V1_SCHEMA)
    assert tuple(declaration) == enforced_inventory

    document = v11_manifest()
    document["adapter_id"] = "mt5"
    document["columns"] = [
        {
            "name": name,
            "dtype": dtype,
            "purpose": (
                "time_key"
                if name == mt5._PHYSICAL_KEY_COLUMN
                else "display_only"
                if name == mt5._DISPLAY_ONLY_COLUMN
                else "value"
            ),
        }
        for name, dtype in declaration.items()
    ]
    manifest = read_manifest(write_manifest(tmp_path / "view", document))
    value_columns = tuple(
        column.name for column in manifest.columns if column.purpose.value == "value"
    )
    enforced = mt5._validate_target_schema(manifest, value_columns)
    assert {name: dtype.name for name, dtype in enforced.items()} == {
        name: declaration[name] for name in value_columns
    }


def test_generation_declaration_exactly_matches_adapter_enforcement(
    tmp_path: Path,
) -> None:
    declaration = cast(
        "Mapping[str, str]", adapter_columns._GENERATION_DTYPES_BY_ADAPTER["mt5"]
    )

    assert tuple(declaration) == (
        "mt5_time_raw",
        "timestamp_mt5_encoded",
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
        "spread",
        "real_volume",
    )
    assert declaration["timestamp_mt5_encoded"] == "datetime64[ns]"
    assert declaration["tick_volume"] == "int64"
    assert declaration["spread"] == "int32"
    assert declaration["real_volume"] == "int64"
    _require_adapter_conformance(tmp_path, declaration)


def test_deliberate_generation_dtype_drift_is_rejected(tmp_path: Path) -> None:
    declaration = dict(adapter_columns._GENERATION_DTYPES_BY_ADAPTER["mt5"])
    declaration["spread"] = "int64"

    with pytest.raises(PartitionReadError, match=r"spread.*requires 'int32'"):
        _require_adapter_conformance(tmp_path, declaration)


def test_generation_declaration_has_no_public_surface_or_package_export() -> None:
    manifests_package = importlib.import_module("trading_research.data.manifests")

    assert adapter_columns.__all__ == []
    assert {
        name for name in vars(adapter_columns) if not name.startswith("_")
    } == {"annotations"}
    assert "adapter_columns" not in manifests_package.__all__
    assert not hasattr(manifests_package, "GENERATION_DTYPES_BY_ADAPTER")
