"""Private column-compatibility primitive shared by fixed research stages."""

from __future__ import annotations

from trading_research.core.errors import ContractViolationError as _ContractViolationError

__all__: list[str] = []


def _require_columns(
    *,
    required: tuple[str, ...],
    available: tuple[str, ...],
    component_label: str,
    artifact_label: str,
) -> None:
    """Refuse missing declared columns before any expensive stage executes."""
    for names, label, allow_empty in (
        (required, "required", True),
        (available, "available", False),
    ):
        if type(names) is not tuple or (not names and not allow_empty):
            raise _ContractViolationError(
                f"{label} columns must be an exact"
                f"{' possibly-empty' if allow_empty else ' non-empty'} tuple"
            )
        if any(type(name) is not str or not name for name in names) or len(
            set(names)
        ) != len(names):
            raise _ContractViolationError(
                f"{label} columns must contain unique non-empty strings"
            )
    if type(component_label) is not str or not component_label:
        raise _ContractViolationError("component_label must be non-empty")
    if type(artifact_label) is not str or not artifact_label:
        raise _ContractViolationError("artifact_label must be non-empty")
    present = frozenset(available)
    missing = tuple(name for name in required if name not in present)
    if missing:
        raise _ContractViolationError(
            f"{component_label} requires missing {artifact_label} columns {missing!r}"
        )
