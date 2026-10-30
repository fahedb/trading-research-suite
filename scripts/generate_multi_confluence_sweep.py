#!/usr/bin/env python3
"""Generate the frozen 300-vector, two-bracket Multi Confluence TRAIN sweep."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "experiments"
    / "multi-confluence-m15-train-plane-b-tp400-sl500-v1.json"
)
OUTPUT_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "sweeps"
    / "multi-confluence-m15-train-300x2-tp400-tp800-sl500-v1.json"
)
SWEEP_ID = "multi-confluence-m15-train-300x2-tp400-tp800-sl500-v1"
BRACKETS = ((400.0, 500.0), (800.0, 500.0))
BTC_OUTPUT_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "sweeps"
    / "multi-confluence-m15-btc-train-300x2-tp18000-tp36000-sl22500-v1.json"
)
BTC_SWEEP_ID = (
    "multi-confluence-m15-btc-train-300x2-tp18000-tp36000-sl22500-v1"
)
BTC_BRACKETS = ((18_000.0, 22_500.0), (36_000.0, 22_500.0))
BTC_V2_OUTPUT_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "sweeps"
    / "multi-confluence-m15-btc-train-300x2-tp18000-tp36000-sl22500-fivefold-v2.json"
)
BTC_V2_SWEEP_ID = (
    "mc-m15-btc-train-300x2-tp18000-tp36000-sl22500-5fold-v2"
)
PORTFOLIO_OUTPUT_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "sweeps"
    / "multi-confluence-m15-xau-portfolio-replay-6x2-v1.json"
)
PORTFOLIO_SWEEP_ID = "multi-confluence-m15-xau-portfolio-replay-6x2-v1"
# Six already-studied XAU vectors, paired under both frozen bracket settings.
# The order keeps each vector's TP400 and TP800 replay adjacent in Explorer.
PORTFOLIO_SOURCE_CHILD_ORDINALS = (
    0,
    300,
    75,
    375,
    100,
    400,
    160,
    460,
    197,
    497,
    289,
    589,
)


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
    ("pivot_left", (2, 3, 5)),
    ("pivot_right", (2, 3, 5)),
    ("rsi_length", (7, 14, 21, 28)),
    ("atr_length", (7, 14, 21, 28)),
    ("level_atr_range", (0.30, 0.45, 0.60, 0.75, 0.90)),
    ("trend_atr_range", (0.25, 0.50, 0.75, 1.00)),
    ("liquidity_lookback", (10, 20, 30, 40, 60)),
    ("minimum_score", (3, 4, 5)),
)

PROFILE_DOMAINS: tuple[tuple[dict[str, object], ...], ...] = (
    (
        {"rsi_long_level": 35, "rsi_short_level": 65},
        {"rsi_long_level": 40, "rsi_short_level": 60},
        {"rsi_long_level": 45, "rsi_short_level": 55},
        {"rsi_long_level": 48, "rsi_short_level": 52},
    ),
    (
        {"ema_fast_length": 20, "ema_slow_length": 100},
        {"ema_fast_length": 20, "ema_slow_length": 200},
        {"ema_fast_length": 50, "ema_slow_length": 100},
        {"ema_fast_length": 50, "ema_slow_length": 200},
        {"ema_fast_length": 100, "ema_slow_length": 200},
    ),
)


def _signature(vector: dict[str, object]) -> str:
    return json.dumps(vector, sort_keys=True, separators=(",", ":"))


def _strategy_vectors(
    base_params: dict[str, object], *, count: int = 300
) -> list[dict[str, object]]:
    if count < 1:
        raise ValueError("count must be positive")
    vectors = [deepcopy(base_params)]
    seen = {_signature(vectors[0])}
    primes = iter(_primes(len(DIRECT_DOMAINS) + len(PROFILE_DOMAINS)))
    direct = tuple((name, values, next(primes)) for name, values in DIRECT_DOMAINS)
    profiles = tuple((values, next(primes)) for values in PROFILE_DOMAINS)
    candidate_index = 1
    while len(vectors) < count:
        vector = deepcopy(base_params)
        for name, values, prime in direct:
            vector[name] = _canonical(_select(candidate_index, prime, values))
        for values, prime in profiles:
            profile = _select(candidate_index, prime, values)
            for name, value in profile.items():
                vector[name] = _canonical(value)
        signature = _signature(vector)
        if signature not in seen:
            vectors.append(vector)
            seen.add(signature)
        candidate_index += 1
        if candidate_index > 100_000:
            raise RuntimeError("unable to generate the requested number of unique vectors")
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
                    _float(stop_loss)
                    for _take_profit, stop_loss in BRACKETS
                    for _vector in vectors
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


def build_btc_request() -> dict[str, object]:
    """Project the frozen strategy vectors onto the approved BTC TRAIN setup."""
    document = deepcopy(build_request())
    document["sweep_id"] = BTC_SWEEP_ID
    base_config = cast("dict[str, object]", document["base_config"])
    base_config["dataset"] = {
        "dataset_id": "BTCUSD_S_M1_TRAIN_V1",
        "manifest_sha256": (
            "73454e8dacf36b233144f1d7b7866ec30cf0c60f7a47efd366c43bc63b632b9b"
        ),
        "role": "train",
    }
    base_config["split_policy"] = {
        "adr": "ADR-0012",
        "policy_version": "btc-v1",
        "policy_sha256": (
            "3896cd9bedfe9357279c02df4904cda92d698b2d079c65e7bed6455503d55899"
        ),
    }
    base_config["bounds"] = {
        "context_start": 1_577_923_200,
        "eval_start": 1_619_598_600,
        "eval_end": 1_704_066_960,
    }
    base_config["folds"] = {
        "fit_rows": 3,
        "eval_rows": 279_421,
        "step_rows": 279_421,
        "embargo_rows": 0,
        "purge_override": None,
    }
    execution = cast("dict[str, object]", base_config["execution"])
    execution["take_profit_pips"] = _float(BTC_BRACKETS[0][0])
    execution["stop_loss_pips"] = _float(BTC_BRACKETS[0][1])
    execution["cost_binding"] = {
        "binding_id": "unverified-audit-input-v1",
        "binding_digest": None,
        "cost_semantics": "unverified",
        "execution_price_model": "recorded_path_unverified",
        "contract_size": _float(1.0),
        "price_units_per_pip": _float(0.01),
        "account_currency_per_quote_currency": _float(1.0),
        "spread_pips": _float(600.0),
        "commission_per_lot": _float(0.0),
        "commission_basis": "per_round_turn",
    }
    axes = cast("list[dict[str, object]]", document["axes"])
    axes[-2]["values"] = [
        _float(take_profit)
        for take_profit, _stop_loss in BTC_BRACKETS
        for _vector in range(300)
    ]
    axes[-1]["values"] = [
        _float(stop_loss)
        for _take_profit, stop_loss in BTC_BRACKETS
        for _vector in range(300)
    ]
    return document


def build_btc_v2_request() -> dict[str, object]:
    """Correct the V1 fold geometry to include five fit-prefixed folds."""
    document = deepcopy(build_btc_request())
    document["sweep_id"] = BTC_V2_SWEEP_ID
    base_config = cast("dict[str, object]", document["base_config"])
    folds = cast("dict[str, object]", base_config["folds"])
    folds["eval_rows"] = 279_420
    folds["step_rows"] = 279_420
    return document


def build_portfolio_request() -> dict[str, object]:
    """Build a 12-child display replay from the frozen XAU sweep."""
    source = build_request()
    source_axes = cast("list[dict[str, object]]", source["axes"])
    axes: list[dict[str, object]] = []
    for axis in source_axes:
        values = cast("list[object]", axis["values"])
        axes.append(
            {
                "json_pointer": axis["json_pointer"],
                "values": [
                    deepcopy(values[ordinal])
                    for ordinal in PORTFOLIO_SOURCE_CHILD_ORDINALS
                ],
            }
        )
    return {
        "schema": "research-sweep-request-v1",
        "sweep_id": PORTFOLIO_SWEEP_ID,
        "base_config": deepcopy(source["base_config"]),
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
    btc_rendered = _render(build_btc_request())
    btc_v2_rendered = _render(build_btc_v2_request())
    portfolio_rendered = _render(build_portfolio_request())
    if args.check:
        if not OUTPUT_PATH.exists() or OUTPUT_PATH.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"stale generated sweep request: {OUTPUT_PATH}")
        if (
            not BTC_OUTPUT_PATH.exists()
            or BTC_OUTPUT_PATH.read_text(encoding="utf-8") != btc_rendered
        ):
            raise SystemExit(f"stale generated sweep request: {BTC_OUTPUT_PATH}")
        if (
            not BTC_V2_OUTPUT_PATH.exists()
            or BTC_V2_OUTPUT_PATH.read_text(encoding="utf-8") != btc_v2_rendered
        ):
            raise SystemExit(f"stale generated sweep request: {BTC_V2_OUTPUT_PATH}")
        if (
            not PORTFOLIO_OUTPUT_PATH.exists()
            or PORTFOLIO_OUTPUT_PATH.read_text(encoding="utf-8")
            != portfolio_rendered
        ):
            raise SystemExit(f"stale generated sweep request: {PORTFOLIO_OUTPUT_PATH}")
        return 0
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    BTC_OUTPUT_PATH.write_text(btc_rendered, encoding="utf-8")
    BTC_V2_OUTPUT_PATH.write_text(btc_v2_rendered, encoding="utf-8")
    PORTFOLIO_OUTPUT_PATH.write_text(portfolio_rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
