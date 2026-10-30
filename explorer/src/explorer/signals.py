"""Map each trade back to the signal that caused it, using recorded facts only.

The join is not a guess. `simulation/bracket.py` reads an intent before it fills,
so a trade's signal is the greatest recorded model-output key strictly before
its ``entry_key``. Dense model outputs also contain the fill row; sparse model
outputs contain only signal rows. The same predecessor rule covers both shapes,
and `tests/test_signals.py` re-checks it against the raw artifacts.

What is returned is a transcription of columns the feature provider already
declared and the run already stored in ``features.npz``. Nothing here re-runs a
strategy, and anything the provider computed but did not declare is reported as
unavailable rather than reconstructed.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from explorer.trades import (
    TRADE_ARTIFACT,
    RunNotFoundError,
    run_dir,
    trade_log,
)

FEATURES = "features.npz"
RUN_FRAME_FORMAT = "trading-research-run-frame"
RUN_FRAME_VERSION = 1

# Zone Trader renders specially, so the Explorer needs to recognise its columns.
# This is a *rendering* convention local to the viewer, not a platform contract:
# the generic path below carries every declared column regardless of provider.
ZONE_PREFIX = "zt_"
ZONE_KINDS = {0: "volume", 1: "fvg", 2: "demand_supply"}
ULS_PROVIDER = "uls_push_v7"
ULS_SOURCES = {
    1: "ULS · IFVG",
    2: "ULS · Push zone",
    3: "Push",
}

# Context the Zone Trader provider computes but never declares as an output
# column, so it is genuinely absent from the run and must not be reconstructed.
# Naming it in the payload is part of showing context honestly.
UNRECORDED = (
    "zone creation time",
    "zone break / sweep count",
    "live zone inventory",
    "stable zone identifier",
)


def _read_frame(path: Path, members: tuple[str, ...] | None = None) -> tuple[dict, dict]:
    """Read a run-frame artifact, validating its declared format."""
    with np.load(path, allow_pickle=False) as payload:
        meta = json.loads(payload["__meta__"].tobytes().decode("ascii"))
        if meta.get("format") != RUN_FRAME_FORMAT:
            raise RunNotFoundError(f"unexpected artifact format in {path.name}")
        if meta.get("format_version") != RUN_FRAME_VERSION:
            raise RunNotFoundError(f"unsupported run-frame version in {path.name}")
        wanted = members if members is not None else tuple(
            name for name in payload.files if name != "__meta__"
        )
        arrays = {name: payload[name] for name in wanted}
    return meta, arrays


def _float_or_none(value: float) -> float | None:
    """NaN means the provider recorded no value on this row."""
    return None if not np.isfinite(value) else float(value)


@lru_cache(maxsize=4)
def signal_context(run_id: str) -> dict[str, Any]:
    """Return the decision-time context for every trade in one run.

    Feature columns are stored uncompressed inside ``features.npz``, so the whole
    frame is read once, projected down to the handful of rows that carry an event,
    and the large arrays are dropped. A whole run reduces to a couple of thousand
    rows.
    """
    directory = run_dir(run_id)
    artifacts = directory / "artifacts"
    features_path = artifacts / FEATURES

    if not features_path.is_file():
        return {
            "run_id": run_id,
            "available": False,
            "reason": "this run did not retain a features artifact",
            "provider": None,
            "columns": [],
            "unrecorded": list(UNRECORDED),
            "contexts": {},
        }

    meta, arrays = _read_frame(features_path)
    names = [column["name"] for column in meta["columns"]]
    members = [column["member"] for column in meta["columns"]]
    origin = meta["columns"][0].get("origin", {}) if meta["columns"] else {}
    provider_id = origin.get("provider_id")
    params = origin.get("params", {})

    feature_keys = arrays["keys"]
    columns = {name: arrays[member] for name, member in zip(names, members, strict=True)}

    # Reduce to event rows before anything else, so the big arrays can be released.
    event_name = next((name for name in names if name.endswith("event_signed")), None)
    if event_name is not None:
        event = columns[event_name]
        event_rows = np.flatnonzero(np.isfinite(event) & (event != 0.0))
    else:
        # Generic fallback: any row where some declared column carries a value.
        finite = np.zeros(feature_keys.size, dtype=bool)
        for values in columns.values():
            finite |= np.isfinite(values)
        event_rows = np.flatnonzero(finite)

    event_keys = feature_keys[event_rows]
    event_values = {name: values[event_rows] for name, values in columns.items()}
    del arrays, columns, feature_keys

    log = trade_log(run_id)
    contexts: dict[str, Any] = {}

    for artifact in sorted(artifacts.glob("fold-*-trades.npz")):
        match = TRADE_ARTIFACT.match(artifact.name)
        if match is None:
            continue
        fold_index = int(match.group(1))
        execution_path = artifacts / f"fold-{fold_index:04d}-model-output.npz"
        if not execution_path.is_file():
            continue
        _, execution = _read_frame(execution_path, members=("keys",))
        execution_keys = execution["keys"]

        with np.load(artifact, allow_pickle=False) as payload:
            entry_keys = payload["entry_key"]

        # Dense outputs contain the fill row while sparse outputs contain only
        # signal rows. In both cases the signal is the greatest output key that
        # is strictly earlier than the recorded fill.
        positions = np.searchsorted(execution_keys, entry_keys)
        for offset, entry_key in enumerate(entry_keys):
            position = int(positions[offset])
            if position <= 0:
                continue
            signal_key = int(execution_keys[position - 1])

            found = int(np.searchsorted(event_keys, signal_key))
            if found >= event_keys.size or int(event_keys[found]) != signal_key:
                continue

            values = {
                name: _float_or_none(series[found])
                for name, series in event_values.items()
            }
            contexts[f"{fold_index}:{int(entry_key)}"] = {
                "signal_key": signal_key,
                "values": values,
            }

    # Attach the context to each trade by its position in the run's trade list.
    by_index: dict[str, Any] = {}
    for trade in log["trades"]:
        found = contexts.get(f"{trade['fold_index']}:{trade['entry_key']}")
        if found is None:
            continue
        by_index[str(trade["index"])] = _describe(found, trade, provider_id, params)

    return {
        "run_id": run_id,
        "available": True,
        "provider": {"provider_id": provider_id, "params": _plain_params(params)},
        "columns": names,
        "unrecorded": list(UNRECORDED) if provider_id == "zone_trader_v13" else [],
        "matched": len(by_index),
        "trade_count": log["count"],
        "contexts": by_index,
    }


def _plain_params(params: dict[str, Any]) -> dict[str, Any]:
    """Decode the platform's float-hex parameter encoding for display."""
    plain: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, dict) and "__float_hex__" in value:
            plain[key] = float.fromhex(value["__float_hex__"])
        else:
            plain[key] = value
    return plain


def _describe(
    found: dict[str, Any],
    trade: dict[str, Any],
    provider_id: str | None,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Shape one trade's recorded decision context for the viewer."""
    values = found["values"]
    context: dict[str, Any] = {"signal_key": found["signal_key"]}

    if provider_id == ULS_PROVIDER:
        source_value = values.get("up_source")
        source_code = None if source_value is None else int(source_value)
        event_value = values.get("up_event_signed")
        context.update({
            "kind": "uls_push",
            "event_signed": None if event_value is None else int(event_value),
            "source": {
                "code": source_code,
                "label": ULS_SOURCES.get(source_code, "ULS + Push"),
            },
            "score": values.get("up_score"),
            "entry_ref": values.get("up_entry_ref"),
            "poi": {
                "top": values.get("up_poi_top"),
                "bottom": values.get("up_poi_bot"),
            },
            "setup": {
                "sweep_extreme": values.get("up_sweep_extreme"),
                "structure": values.get("up_struct_level"),
                "opposite_liquidity": values.get("up_opp_liquidity"),
                "liquidity_price": values.get("up_liq_price"),
                "liquidity_touches": values.get("up_liq_touches"),
                "sweep_distance_atr": values.get("up_sweep_dist_atr"),
                "wick_ratio": values.get("up_wick_ratio"),
                "displacement_atr": values.get("up_disp_atr"),
                "volume_relative": values.get("up_vol_rel"),
                "volatility_rank": values.get("up_vol_rank"),
                "atr_execution": values.get("up_atr_exec"),
                "atr_signal": values.get("up_atr_sig"),
            },
            "confirmations": {
                "bos": values.get("up_bos"),
                "trendline": values.get("up_tl"),
                "cisd": values.get("up_cisd"),
                "key_level": values.get("up_key_ok"),
            },
            "executed": {
                "entry": trade["entry_price"],
                "stop": trade["stop_loss"],
                "target": trade["take_profit"],
            },
            "values": values,
        })
        return context

    if provider_id != "zone_trader_v13":
        # Generic path: carry every declared column through untouched, so a future
        # provider needs no Explorer change to have its context listed.
        context["values"] = values
        return context

    kind_value = values.get(f"{ZONE_PREFIX}zone_kind")
    context["zone"] = {
        "kind": ZONE_KINDS.get(int(kind_value)) if kind_value is not None else None,
        "kind_code": None if kind_value is None else int(kind_value),
        "top": values.get(f"{ZONE_PREFIX}zone_top"),
        "bottom": values.get(f"{ZONE_PREFIX}zone_bottom"),
        # The provider drops event_zone_created_at, so the zone's start is unknown.
        # It is shown from the signal onward and labelled, never back-dated.
        "created_key": None,
        "known_from": found["signal_key"],
    }
    context["intended"] = {
        "entry": values.get(f"{ZONE_PREFIX}entry_price"),
        "stop": values.get(f"{ZONE_PREFIX}stop_price"),
        "target": values.get(f"{ZONE_PREFIX}target_price"),
    }
    context["executed"] = {
        "entry": trade["entry_price"],
        "stop": trade["stop_loss"],
        "target": trade["take_profit"],
    }
    event = values.get(f"{ZONE_PREFIX}event_signed")
    context["event_signed"] = None if event is None else int(event)
    context["confirmation_mode"] = params.get("confirmation_mode")
    context["max_bars_wait"] = params.get("max_bars_wait")
    return context
