"""Tests for the 5 follow-up knob kinds: lag, threshold, set_membership,
penalty_magnitude, inaction_score."""
from __future__ import annotations

import pytest

from engine.autoresearcher.knobs import KnobError
from engine.autoresearcher.knob_kinds.inaction_score import (
    InactionScoreKnob,
    InactionScoreState,
)
from engine.autoresearcher.knob_kinds.penalty_magnitude import (
    PenaltyMagnitudeKnob,
    PenaltyMagnitudeState,
)
from engine.autoresearcher.knob_kinds.primitive_lag import (
    PrimitiveLagKnob,
    PrimitiveLagState,
)
from engine.autoresearcher.knob_kinds.risk_threshold import (
    RiskThresholdKnob,
    RiskThresholdState,
)
from engine.autoresearcher.knob_kinds.set_membership import (
    SetMembershipKnob,
    SetMembershipState,
)


# ---------------------------------------------------------------------------
# PrimitiveLagKnob
# ---------------------------------------------------------------------------


def test_primitive_lag_round_trip():
    s = PrimitiveLagState(values={"edge_EP_OX": 2.0})
    k = PrimitiveLagKnob(edge_id="edge_EP_OX", state=s)
    k.apply(3.0)
    assert k.current_value() == 3.0
    k.revert()
    assert k.current_value() == 2.0


def test_primitive_lag_rejects_negative():
    s = PrimitiveLagState(values={"e": 2.0})
    k = PrimitiveLagKnob(edge_id="e", state=s)
    with pytest.raises(KnobError, match=r"\[0, 12\]"):
        k.apply(-1.0)


def test_primitive_lag_rejects_too_big():
    s = PrimitiveLagState(values={"e": 5.0})
    k = PrimitiveLagKnob(edge_id="e", state=s, magnitude=1.0)
    with pytest.raises(KnobError, match="magnitude"):
        k.apply(8.0)


# ---------------------------------------------------------------------------
# RiskThresholdKnob
# ---------------------------------------------------------------------------


def test_risk_threshold_round_trip():
    s = RiskThresholdState(values={"tau_drought": -3.0})
    k = RiskThresholdKnob(threshold_id="tau_drought", state=s, magnitude=0.5)
    k.apply(-2.8)
    assert k.current_value() == -2.8
    k.revert()
    assert k.current_value() == -3.0


def test_risk_threshold_enforces_magnitude():
    s = RiskThresholdState(values={"tau_x": 1.0})
    k = RiskThresholdKnob(threshold_id="tau_x", state=s, magnitude=0.1)
    with pytest.raises(KnobError, match="magnitude"):
        k.apply(2.0)


# ---------------------------------------------------------------------------
# SetMembershipKnob
# ---------------------------------------------------------------------------


def test_set_membership_add_round_trip():
    s = SetMembershipState(sets={
        ("triggersRiskCategory", "topic_water"): frozenset({"physical"}),
    })
    k = SetMembershipKnob(
        predicate="triggersRiskCategory", subject="topic_water",
        member="regulatory", action="add", state=s,
    )
    k.apply()
    assert "regulatory" in s.get("triggersRiskCategory", "topic_water")
    k.revert()
    assert "regulatory" not in s.get("triggersRiskCategory", "topic_water")


def test_set_membership_remove_round_trip():
    s = SetMembershipState(sets={
        ("triggersTEMPLES", "topic_cyber"): frozenset({"Tech", "Economic"}),
    })
    k = SetMembershipKnob(
        predicate="triggersTEMPLES", subject="topic_cyber",
        member="Tech", action="remove", state=s,
    )
    k.apply()
    assert "Tech" not in s.get("triggersTEMPLES", "topic_cyber")
    k.revert()
    assert "Tech" in s.get("triggersTEMPLES", "topic_cyber")


def test_set_membership_rejects_bad_action():
    s = SetMembershipState()
    with pytest.raises(KnobError, match="action"):
        SetMembershipKnob(
            predicate="p", subject="s", member="m",
            action="invert", state=s,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# PenaltyMagnitudeKnob
# ---------------------------------------------------------------------------


def test_penalty_magnitude_round_trip():
    s = PenaltyMagnitudeState(values={"staleness": 0.15})
    k = PenaltyMagnitudeKnob(penalty="staleness", state=s, magnitude=0.05)
    k.apply(0.18)
    assert abs(k.current_value() - 0.18) < 1e-9
    k.revert()
    assert k.current_value() == 0.15


def test_penalty_magnitude_rejects_unknown_penalty():
    s = PenaltyMagnitudeState()
    with pytest.raises(KnobError, match="unknown penalty"):
        PenaltyMagnitudeKnob(penalty="bogus", state=s)


def test_penalty_magnitude_rejects_out_of_clamp():
    s = PenaltyMagnitudeState(values={"confidence": 0.20})
    k = PenaltyMagnitudeKnob(penalty="confidence", state=s, magnitude=10.0)
    # within magnitude bound but outside [0, 0.5] soft clamp
    with pytest.raises(KnobError, match=r"\[0, 0\.5\]"):
        k.apply(0.8)


# ---------------------------------------------------------------------------
# InactionScoreKnob
# ---------------------------------------------------------------------------


def test_inaction_base_score_round_trip():
    s = InactionScoreState(values={("base", "CRITICAL"): 25.0})
    k = InactionScoreKnob(kind="base", slot="CRITICAL", state=s)
    k.apply(26.0)
    assert k.current_value() == 26.0
    k.revert()
    assert k.current_value() == 25.0


def test_inaction_rec_type_bonus_round_trip():
    s = InactionScoreState(values={("rec_type_bonus", "compliance"): 3.0})
    k = InactionScoreKnob(kind="rec_type_bonus", slot="compliance", state=s)
    k.apply(4.0)
    assert k.current_value() == 4.0
    k.revert()


def test_inaction_score_rejects_invalid_kind():
    s = InactionScoreState()
    with pytest.raises(KnobError, match="kind"):
        InactionScoreKnob(kind="bonus", slot="x", state=s)  # type: ignore[arg-type]


def test_inaction_base_score_rejects_out_of_clamp():
    s = InactionScoreState(values={("base", "HIGH"): 30.0})
    k = InactionScoreKnob(kind="base", slot="HIGH", state=s, magnitude=100.0)
    with pytest.raises(KnobError, match=r"\[0, 50\]"):
        k.apply(80.0)


def test_inaction_rec_bonus_rejects_out_of_clamp():
    s = InactionScoreState(values={("rec_type_bonus", "x"): 2.0})
    k = InactionScoreKnob(kind="rec_type_bonus", slot="x", state=s, magnitude=100.0)
    with pytest.raises(KnobError, match=r"\[-5, 10\]"):
        k.apply(20.0)
