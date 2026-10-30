"""Pure, event-driven V1 simulation over platform-issued core carriers."""

from trading_research.simulation.bracket import (
    SUPPORTED_INTENT_SCHEMA_IDS,
    simulate_bracket_non_interruptible_v1,
    simulate_bracket_single_position_v1,
)

# Import machinery binds the loaded submodule on its package. Keep the accepted
# package surface to the two declarations below, rather than exposing a third
# conventional public name as an implementation accident.
globals().pop("bracket", None)

__all__ = [
    "SUPPORTED_INTENT_SCHEMA_IDS",
    "simulate_bracket_non_interruptible_v1",
    "simulate_bracket_single_position_v1",
]
