#!/usr/bin/env python3
"""Generate the frozen 200-vector ULS + PUSH four-bracket TRAIN sweep."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG_PATH = (
    REPOSITORY_ROOT / "config" / "experiments" / "uls-push-v7-train-plane-b-tp400-sl400-v1.json"
)
OUTPUT_PATH = (
    REPOSITORY_ROOT / "config" / "sweeps" / "uls-push-v7-train-owner-200x4-brackets-v1.json"
)
SWEEP_ID = "uls-push-v7-train-owner-200x4-brackets-v1"
BRACKETS = ((200.0, 200.0), (800.0, 800.0), (600.0, 300.0), (300.0, 600.0))


def _float(value: float) -> dict[str, str]:
    return {"__float_hex__": float(value).hex()}


def _canonical(value: object) -> object:
    if isinstance(value, float):
        return _float(value)
    return value


def _radical_inverse(index: int, base: int) -> float:
    result = 0.0
    factor = 1.0 / base
    while index:
        index, digit = divmod(index, base)
        result += digit * factor
        factor /= base
    return result


def _select[T](index: int, prime: int, values: tuple[T, ...]) -> T:
    position = min(int(_radical_inverse(index, prime) * len(values)), len(values) - 1)
    return deepcopy(values[position])


def _primes(count: int) -> tuple[int, ...]:
    values: list[int] = []
    candidate = 2
    while len(values) < count:
        if all(candidate % divisor for divisor in range(2, int(candidate**0.5) + 1)):
            values.append(candidate)
        candidate += 1
    return tuple(values)


DIRECT_DOMAINS: tuple[tuple[str, tuple[object, ...]], ...] = (
    ("atr_len", (10, 14, 20, 28)),
    ("pivot_len", (3, 5, 8)),
    ("liquidity_mode", ("structural", "equal", "both")),
    ("max_levels", (10, 20, 40)),
    ("equal_tolerance_atr", (0.05, 0.10, 0.20)),
    ("min_equal_touches", (2, 3)),
    ("liquidity_age", (400, 800, 1_200)),
    ("cisd_max_age", (400, 800, 1_200)),
    ("sweep_confirmation", ("wick", "close", "close_confirm")),
    ("return_bars", (2, 3, 5)),
    ("ifvg_search", (3, 6, 10)),
    ("inversion_mode", ("wick", "close")),
    ("max_ifvg_distance_atr", (1.0, 2.0, 3.0)),
    ("fvg_age", (150, 300, 500)),
    ("push_bars", (4, 6, 10)),
    ("push_atr", (1.0, 1.5, 2.0)),
    ("push_volume_multiple", (1.0, 1.5, 2.0)),
    ("zone_uses_wicks", (True, False)),
    ("max_push_zones", (3, 6, 10)),
    ("push_zone_age", (100, 200, 400)),
    (
        "confirmation_mode",
        ("trendline", "bos", "cisd", "trendline_or_bos", "any", "two"),
    ),
    ("confirmation_wait", (6, 12, 20)),
    ("min_trendline_touches", (2, 3)),
    ("trendline_tolerance_atr", (0.05, 0.10, 0.20)),
    ("trendline_break_mode", ("wick", "close")),
    ("total_setup_bars", (20, 40, 60)),
    ("cancel_opposite", (True, False)),
)

PROFILE_DOMAINS: tuple[tuple[dict[str, object], ...], ...] = (
    (
        {"min_sweep_atr": 0.05, "max_sweep_atr": 0.50},
        {"min_sweep_atr": 0.10, "max_sweep_atr": 0.75},
        {"min_sweep_atr": 0.20, "max_sweep_atr": 1.00},
    ),
    (
        {"use_ifvg_size": False, "min_ifvg_atr": 0.05, "max_ifvg_atr": 1.00},
        {"use_ifvg_size": True, "min_ifvg_atr": 0.05, "max_ifvg_atr": 0.75},
        {"use_ifvg_size": True, "min_ifvg_atr": 0.10, "max_ifvg_atr": 1.25},
    ),
    (
        {"min_retracement_pct": 15.0, "max_retracement_pct": 75.0},
        {"min_retracement_pct": 25.0, "max_retracement_pct": 90.0},
        {"min_retracement_pct": 40.0, "max_retracement_pct": 100.0},
    ),
    (
        {"require_rejection": False, "rejection_ratio": 0.45},
        {"require_rejection": True, "rejection_ratio": 0.30},
        {"require_rejection": True, "rejection_ratio": 0.45},
        {"require_rejection": True, "rejection_ratio": 0.60},
    ),
    (
        {
            "use_momentum": False,
            "momentum_len": 50,
            "momentum_slope": 5,
            "momentum_window": 500,
        },
        {
            "use_momentum": True,
            "momentum_len": 20,
            "momentum_slope": 3,
            "momentum_window": 500,
        },
        {
            "use_momentum": True,
            "momentum_len": 50,
            "momentum_slope": 5,
            "momentum_window": 500,
        },
        {
            "use_momentum": True,
            "momentum_len": 100,
            "momentum_slope": 10,
            "momentum_window": 1_000,
        },
    ),
    (
        {
            "use_key_levels": False,
            "require_key_level": False,
            "key_level_atr_multiple": 1.5,
            "key_level_tolerance_atr": 0.25,
        },
        {
            "use_key_levels": True,
            "require_key_level": False,
            "key_level_atr_multiple": 1.0,
            "key_level_tolerance_atr": 0.15,
        },
        {
            "use_key_levels": True,
            "require_key_level": False,
            "key_level_atr_multiple": 1.5,
            "key_level_tolerance_atr": 0.25,
        },
        {
            "use_key_levels": True,
            "require_key_level": True,
            "key_level_atr_multiple": 1.0,
            "key_level_tolerance_atr": 0.25,
        },
        {
            "use_key_levels": True,
            "require_key_level": True,
            "key_level_atr_multiple": 2.0,
            "key_level_tolerance_atr": 0.35,
        },
    ),
    (
        {
            "ema_filter": "off",
            "ema_len": 200,
            "ema_window": 2_000,
            "use_volume_filter": False,
            "volume_multiple": 1.2,
            "use_displacement_filter": False,
            "displacement_atr": 0.5,
            "use_score_filter": False,
            "min_score": 60,
            "min_volatility_rank": 0,
        },
        {
            "ema_filter": "current",
            "ema_len": 50,
            "ema_window": 2_000,
            "use_volume_filter": False,
            "volume_multiple": 1.2,
            "use_displacement_filter": False,
            "displacement_atr": 0.5,
            "use_score_filter": False,
            "min_score": 60,
            "min_volatility_rank": 0,
        },
        {
            "ema_filter": "current",
            "ema_len": 100,
            "ema_window": 2_000,
            "use_volume_filter": False,
            "volume_multiple": 1.2,
            "use_displacement_filter": False,
            "displacement_atr": 0.5,
            "use_score_filter": False,
            "min_score": 60,
            "min_volatility_rank": 0,
        },
        {
            "ema_filter": "current",
            "ema_len": 200,
            "ema_window": 2_000,
            "use_volume_filter": False,
            "volume_multiple": 1.2,
            "use_displacement_filter": False,
            "displacement_atr": 0.5,
            "use_score_filter": False,
            "min_score": 60,
            "min_volatility_rank": 0,
        },
        {
            "ema_filter": "off",
            "ema_len": 200,
            "ema_window": 2_000,
            "use_volume_filter": True,
            "volume_multiple": 1.0,
            "use_displacement_filter": False,
            "displacement_atr": 0.5,
            "use_score_filter": False,
            "min_score": 60,
            "min_volatility_rank": 0,
        },
        {
            "ema_filter": "off",
            "ema_len": 200,
            "ema_window": 2_000,
            "use_volume_filter": True,
            "volume_multiple": 1.5,
            "use_displacement_filter": False,
            "displacement_atr": 0.5,
            "use_score_filter": False,
            "min_score": 60,
            "min_volatility_rank": 0,
        },
        {
            "ema_filter": "off",
            "ema_len": 200,
            "ema_window": 2_000,
            "use_volume_filter": False,
            "volume_multiple": 1.2,
            "use_displacement_filter": True,
            "displacement_atr": 0.25,
            "use_score_filter": False,
            "min_score": 60,
            "min_volatility_rank": 0,
        },
        {
            "ema_filter": "off",
            "ema_len": 200,
            "ema_window": 2_000,
            "use_volume_filter": False,
            "volume_multiple": 1.2,
            "use_displacement_filter": True,
            "displacement_atr": 0.75,
            "use_score_filter": False,
            "min_score": 60,
            "min_volatility_rank": 0,
        },
        {
            "ema_filter": "off",
            "ema_len": 200,
            "ema_window": 2_000,
            "use_volume_filter": False,
            "volume_multiple": 1.2,
            "use_displacement_filter": False,
            "displacement_atr": 0.5,
            "use_score_filter": True,
            "min_score": 50,
            "min_volatility_rank": 0,
        },
        {
            "ema_filter": "off",
            "ema_len": 200,
            "ema_window": 2_000,
            "use_volume_filter": False,
            "volume_multiple": 1.2,
            "use_displacement_filter": False,
            "displacement_atr": 0.5,
            "use_score_filter": True,
            "min_score": 70,
            "min_volatility_rank": 0,
        },
        {
            "ema_filter": "off",
            "ema_len": 200,
            "ema_window": 2_000,
            "use_volume_filter": False,
            "volume_multiple": 1.2,
            "use_displacement_filter": False,
            "displacement_atr": 0.5,
            "use_score_filter": False,
            "min_score": 60,
            "min_volatility_rank": 25,
        },
        {
            "ema_filter": "off",
            "ema_len": 200,
            "ema_window": 2_000,
            "use_volume_filter": False,
            "volume_multiple": 1.2,
            "use_displacement_filter": False,
            "displacement_atr": 0.5,
            "use_score_filter": False,
            "min_score": 60,
            "min_volatility_rank": 50,
        },
    ),
)


def _strategy_vectors(
    base_params: dict[str, object], *, count: int = 200
) -> list[dict[str, object]]:
    if count < 1:
        raise ValueError("count must be positive")
    vectors = [deepcopy(base_params)]
    primes = iter(_primes(len(DIRECT_DOMAINS) + len(PROFILE_DOMAINS)))
    direct = tuple((name, values, next(primes)) for name, values in DIRECT_DOMAINS)
    profiles = tuple((values, next(primes)) for values in PROFILE_DOMAINS)
    for index in range(1, count):
        vector = deepcopy(base_params)
        vector["engine_mode"] = "merged" if index % 2 == 0 else "parallel"
        for name, values, prime in direct:
            vector[name] = _canonical(_select(index, prime, values))
        atr_len = vector["atr_len"]
        assert isinstance(atr_len, int)
        vector["atr_window"] = max(300, 20 * atr_len)
        for values, prime in profiles:
            profile = _select(index, prime, values)
            for name, value in profile.items():
                vector[name] = _canonical(value)
        vectors.append(vector)
    return vectors


def build_request() -> dict[str, object]:
    """Return the complete deterministic sweep request document."""
    base_config = json.loads(BASE_CONFIG_PATH.read_text(encoding="utf-8"))
    base_params = base_config["features"][0]["params"]
    vectors = _strategy_vectors(base_params)
    parameter_names = sorted(
        name for name in base_params if any(vector[name] != base_params[name] for vector in vectors)
    )
    expanded_vectors = [vector for _bracket in BRACKETS for vector in vectors]
    axes = [
        {
            "json_pointer": f"/features/0/params/{name}",
            "values": [vector[name] for vector in expanded_vectors],
        }
        for name in parameter_names
    ]
    axes.extend(
        (
            {
                "json_pointer": "/execution/take_profit_pips",
                "values": [
                    _float(take_profit)
                    for take_profit, _stop_loss in BRACKETS
                    for _vector in vectors
                ],
            },
            {
                "json_pointer": "/execution/stop_loss_pips",
                "values": [
                    _float(stop_loss) for _take_profit, stop_loss in BRACKETS for _vector in vectors
                ],
            },
        )
    )
    return {
        "schema": "research-sweep-request-v1",
        "sweep_id": SWEEP_ID,
        "base_config": base_config,
        "axes": axes,
        "combination": "zip",
    }


def _render(document: dict[str, object]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"


def main() -> int:
    """Write the request or verify that the checked-in copy is current."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="refuse unless the checked-in request matches deterministic generation",
    )
    args = parser.parse_args()
    rendered = _render(build_request())
    if args.check:
        if not OUTPUT_PATH.exists() or OUTPUT_PATH.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"stale generated sweep request: {OUTPUT_PATH}")
        return 0
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
