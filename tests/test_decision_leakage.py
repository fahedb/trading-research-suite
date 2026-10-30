"""Release-blocking point-in-time rig for DecisionPolicy.

A policy is handed its whole ``ModelOutputFrame`` in one call, so nothing in the
type system stops it from reading rows that lie in the future of the row it is
deciding. That was a tolerable residual risk while a policy could only emit a
sign; it is not tolerable now that a policy may also choose a trade's stop and
target, because a stop placed just beyond a future adverse excursion is both
enormously profitable and entirely plausible-looking.

The rig below is a causality test, not a style check: it re-runs each policy on
every strict prefix of its own input and requires the decision at the cut row to
be bit-identical to the decision the full frame produced. ``_FutureOracle...``
policies exist only to prove the rig actually bites, and the inventory test at
the bottom keeps them out of the production registry.
"""

from __future__ import annotations

import numpy as np
import pytest

from trading_research.core import (
    ClockDomain,
    DatasetIdentity,
    IntentFrame,
    NativeTimeKey,
    StageOrigin,
    TimeUnit,
    ViewSpan,
)
from trading_research.core.research_frames import (
    ModelOutputFrame,
    _make_intent_frame_from_model_output,
    _make_model_output_frame,
)
from trading_research.decisions import DECISION_POLICIES
from trading_research.decisions.reference import (
    BracketThresholdPolicy,
    ThresholdPolicy,
)

DOMAIN = ClockDomain("test.native")
IDENTITY = DatasetIdentity("TRAIN_TEST", "1" * 64)
BRACKETED = "bracketed_exposure_intent_v1"

# A deliberately awkward path: sign changes, exact zeros, a NaN the policies must
# preserve as an abstention, and bracket columns that vary row to row.
SCORES = (-0.9, 0.4, 0.0, 1.6, -0.2, float("nan"), 0.8, -1.4, 0.05, 2.2)
TARGETS = (3.0, 5.0, 1.5, 9.0, 2.5, 4.0, 6.5, 1.25, 8.0, 0.5)
STOPS = (2.0, 1.0, 4.5, 3.5, 7.0, 2.25, 1.75, 5.5, 3.0, 6.0)


class _LeakDetectedError(AssertionError):
    """Raised when the causality rig catches a policy reading its own future."""


def _frame(rows: int, *, strict_events: bool = False) -> ModelOutputFrame:
    keys = tuple(10 + index for index in range(rows))
    scores = (
        tuple(1.0 if index % 2 == 0 else -1.0 for index in range(len(SCORES)))
        if strict_events
        else SCORES
    )
    view = ViewSpan(
        (IDENTITY,),
        TimeUnit.SECONDS,
        DOMAIN,
        NativeTimeKey(keys[0], TimeUnit.SECONDS, DOMAIN),
        NativeTimeKey(keys[0], TimeUnit.SECONDS, DOMAIN),
        NativeTimeKey(keys[-1], TimeUnit.SECONDS, DOMAIN),
    )
    origin = StageOrigin("model_v1", 1, {})
    return _make_model_output_frame(
        view=view,
        keys=np.asarray(keys, dtype=np.int64),
        columns={
            "score": np.asarray(scores[:rows], dtype=np.float64),
            "target_distance": np.asarray(TARGETS[:rows], dtype=np.float64),
            "stop_distance": np.asarray(STOPS[:rows], dtype=np.float64),
            "value_0": np.asarray(TARGETS[:rows], dtype=np.float64),
            "value_1": np.asarray(STOPS[:rows], dtype=np.float64),
            "value_2": np.arange(rows, dtype=np.float64),
            "value_3": np.arange(rows, dtype=np.float64) + 1.0,
            "value_4": np.arange(rows, dtype=np.float64) + 2.0,
            "value_5": np.arange(rows, dtype=np.float64) + 3.0,
            "value_6": np.arange(rows, dtype=np.float64) + 4.0,
        },
        origins={
            "score": origin,
            "target_distance": origin,
            "stop_distance": origin,
            "value_0": origin,
            "value_1": origin,
            "value_2": origin,
            "value_3": origin,
            "value_4": origin,
            "value_5": origin,
            "value_6": origin,
        },
    )


def _row_bits(intent: IntentFrame, key: int) -> tuple[bytes, ...]:
    """Return the exact bit patterns this intent states at one observed key.

    Comparing raw bits rather than values is deliberate: NaN is a load-bearing
    value in every one of these channels, and ``nan == nan`` is false.
    """
    keys = intent.keys
    matches = np.flatnonzero(keys == key)
    if not matches.size:
        return ()
    position = int(matches[0])
    channels = [intent.target_exposure]
    take_profit = intent.take_profit_distance
    stop_loss = intent.stop_loss_distance
    if take_profit is not None and stop_loss is not None:
        channels.extend((take_profit, stop_loss))
    return tuple(
        np.asarray(channel[position], dtype=np.float64).tobytes() for channel in channels
    )


def assert_policy_is_point_in_time(policy: object, *, rows: int = len(SCORES)) -> None:
    """Refuse a policy whose decision at a row depends on any later row.

    For every cut ``t`` the policy sees only rows ``0..t`` and must reach exactly
    the statement it reached at ``t`` when it could also see everything after it.
    """
    strict_events = getattr(policy, "policy_id", None) in {
        "adaptive_bracket_exposure_v1",
        "uls_joint_bracket_exposure_v1",
        "zone_adaptive_bracket_exposure_v1",
    }
    full = _frame(rows, strict_events=strict_events)
    decide = policy.decide  # type: ignore[attr-defined]
    full_intent = decide(full)
    for cut in range(1, rows + 1):
        prefix_intent = decide(_frame(cut, strict_events=strict_events))
        key = int(full.keys[cut - 1])
        prefix_bits = _row_bits(prefix_intent, key)
        full_bits = _row_bits(full_intent, key)
        if prefix_bits != full_bits:
            raise _LeakDetectedError(
                f"{type(policy).__name__} decided differently at key {key} once "
                f"rows after it became visible: {prefix_bits!r} != {full_bits!r}"
            )


# --------------------------------------------------------------------------
# Test-only adversaries. Never registered; see the inventory test below.
# --------------------------------------------------------------------------


class _FutureOracleExposurePolicy(ThresholdPolicy):
    """Choose exposure from the next row's score instead of this row's."""

    def decide(self, model_output: ModelOutputFrame) -> IntentFrame:
        values = model_output.column("score")
        shifted = np.roll(values, -1)
        exposure = np.where(np.isfinite(shifted) & (shifted > 0.0), 1.0, -1.0)
        return _make_intent_frame_from_model_output(
            model_output=model_output,
            intent_schema_id="target_exposure_intent_v1",
            keys=model_output.keys,
            target_exposure=exposure.astype(np.float64),
        )


class _FutureOracleBracketPolicy(BracketThresholdPolicy):
    """Size the stop from a later row, which is the leak that actually pays.

    This is the realistic failure: exposure stays honest and only the bracket
    peeks forward, so the resulting equity curve looks like a well-managed
    strategy rather than an obvious oracle.
    """

    def decide(self, model_output: ModelOutputFrame) -> IntentFrame:
        honest = super().decide(model_output)
        stop = honest.stop_loss_distance
        assert stop is not None
        peeked = np.roll(stop, -1)
        # Keep every value legal so only the causality rig can object.
        peeked = np.where(np.isfinite(peeked) & (peeked > 0.0), peeked, np.nan)
        take_profit = honest.take_profit_distance
        assert take_profit is not None
        return _make_intent_frame_from_model_output(
            model_output=model_output,
            intent_schema_id=BRACKETED,
            keys=honest.keys,
            target_exposure=honest.target_exposure,
            take_profit_distance=take_profit,
            stop_loss_distance=peeked,
        )


ADVERSARY_CLASSES = frozenset({_FutureOracleExposurePolicy, _FutureOracleBracketPolicy})


# --------------------------------------------------------------------------
# The rig, applied to every production policy
# --------------------------------------------------------------------------


# One or more constructor argument sets per registered policy id. The rig builds
# its subjects from the registry rather than from a hand-written list, because a
# hand-written list is exactly what silently lost coverage when two branches each
# added a policy. Registering a policy without adding a recipe here fails the
# inventory test below rather than quietly going unchecked.
POLICY_RECIPES: dict[str, tuple[dict[str, object], ...]] = {
    "adaptive_bracket_exposure_v1": (
        {
            "stop_loss_distance": 8.0,
            "value_0_distance": 2.0,
            "value_1_distance": 3.0,
            "value_2_distance": 4.0,
            "value_3_distance": 5.0,
            "value_4_distance": 6.0,
            "value_5_distance": 7.0,
            "value_6_distance": 8.0,
        },
    ),
    "abstain_v1": ({},),
    "event_exposure_v1": ({},),
    "threshold_v1": ({"output_column": "score", "threshold": 0.5},),
    "uls_joint_bracket_exposure_v1": (
        {
            "stop_loss_distance": 8.0,
            "value_0_distance": 2.0,
            "value_1_distance": 4.0,
            "value_2_distance": 6.0,
            "value_3_distance": 8.0,
        },
    ),
    "bracket_threshold_v1": (
        {"output_column": "score", "threshold": 0.5},
        {
            "output_column": "score",
            "threshold": 0.5,
            "take_profit_column": "target_distance",
            "stop_loss_column": "stop_distance",
        },
        {"output_column": "score", "threshold": 0.0, "stop_loss_column": "stop_distance"},
    ),
    "zone_fixed_bracket_exposure_v1": (
        {"take_profit_distance": 8.0, "stop_loss_distance": 4.0},
        {"take_profit_distance": None, "stop_loss_distance": 4.0},
        {"take_profit_distance": 8.0, "stop_loss_distance": None},
    ),
    "zone_adaptive_bracket_exposure_v1": (
        {
            "stop_loss_distance": 4.0,
            "value_0_distance": 2.0,
            "value_1_distance": 4.0,
            "value_2_distance": 8.0,
        },
    ),
}


def _production_policies() -> tuple[object, ...]:
    """Build every registered policy in every declared configuration."""
    return tuple(
        DECISION_POLICIES.create(name, **kwargs)
        for name in sorted(POLICY_RECIPES)
        for kwargs in POLICY_RECIPES[name]
    )


def test_every_registered_policy_has_a_causality_recipe() -> None:
    """A policy cannot enter the registry without entering this rig."""
    assert set(POLICY_RECIPES) == DECISION_POLICIES.names()
    assert all(POLICY_RECIPES.values()), "every policy needs at least one configuration"


@pytest.mark.parametrize("policy", _production_policies(), ids=lambda p: type(p).__name__)
def test_every_registered_policy_configuration_is_point_in_time(policy: object) -> None:
    assert_policy_is_point_in_time(policy)


def test_the_rig_catches_a_policy_that_reads_a_future_exposure() -> None:
    with pytest.raises(_LeakDetectedError):
        assert_policy_is_point_in_time(
            _FutureOracleExposurePolicy(output_column="score", threshold=0.5)
        )


def test_the_rig_catches_a_policy_that_reads_a_future_bracket() -> None:
    """Non-vacuity for the case this whole rig was added for."""
    with pytest.raises(_LeakDetectedError):
        assert_policy_is_point_in_time(
            _FutureOracleBracketPolicy(
                output_column="score",
                threshold=0.5,
                take_profit_column="target_distance",
                stop_loss_column="stop_distance",
            )
        )


def test_the_rig_compares_bits_so_a_nan_abstention_cannot_hide_a_leak() -> None:
    """A rig that compared values would treat every NaN row as agreement."""
    frame = _frame(len(SCORES))
    policy = BracketThresholdPolicy(
        output_column="score",
        threshold=0.5,
        stop_loss_column="stop_distance",
    )
    intent = policy.decide(frame)
    exposure = intent.target_exposure
    assert bool(np.isnan(exposure).any()), "the fixture must exercise the NaN path"
    nan_key = int(intent.keys[int(np.flatnonzero(np.isnan(exposure))[0])])
    assert _row_bits(intent, nan_key) == _row_bits(intent, nan_key)
    assert _row_bits(intent, nan_key) != _row_bits(intent, int(intent.keys[0]))


def test_adversaries_are_never_registered_as_production_policies() -> None:
    """No adversary is reachable from the registry, by name or by construction."""
    registered = DECISION_POLICIES.names()
    assert registered == {
        "adaptive_bracket_exposure_v1",
        "abstain_v1",
        "bracket_threshold_v1",
        "event_exposure_v1",
        "threshold_v1",
        "uls_joint_bracket_exposure_v1",
        "zone_adaptive_bracket_exposure_v1",
        "zone_fixed_bracket_exposure_v1",
    }
    for policy in _production_policies():
        assert type(policy) not in ADVERSARY_CLASSES


def test_the_adversary_inventory_is_exact() -> None:
    """A new adversary must be declared, so the rig cannot silently lose coverage."""
    assert {cls.__name__ for cls in ADVERSARY_CLASSES} == {
        "_FutureOracleBracketPolicy",
        "_FutureOracleExposurePolicy",
    }
