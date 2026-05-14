"""Phase C — Per-coach evaluate() tests."""
from __future__ import annotations

from engine.advisor.events import (
    DataIngestEvent,
    ForecastShiftEvent,
    RiskArticleEvent,
)
from engine.advisor.personas import DataCoach, ForecastCoach, RiskCoach


# ---------- DataCoach ------------------------------------------------------


def test_data_coach_fires_on_freshness_gap():
    coach = DataCoach()
    hints = coach.evaluate(DataIngestEvent(payload={"tenants_stale": 5}))
    assert len(hints) == 1
    assert hints[0].kind == "freshness_gap"
    assert hints[0].severity == "moderate"


def test_data_coach_silent_when_no_stale():
    coach = DataCoach()
    hints = coach.evaluate(DataIngestEvent(payload={"tenants_stale": 0}))
    assert hints == []


def test_data_coach_fires_on_failures():
    coach = DataCoach()
    hints = coach.evaluate(DataIngestEvent(payload={"failures": ["adani-power"]}))
    assert len(hints) == 1
    assert hints[0].kind == "ingest_failure"
    assert hints[0].severity == "high"


def test_data_coach_ignores_other_events():
    coach = DataCoach()
    assert coach.evaluate(RiskArticleEvent(payload={})) == []


# ---------- RiskCoach ------------------------------------------------------


def test_risk_coach_fires_on_critical():
    coach = RiskCoach()
    hints = coach.evaluate(RiskArticleEvent(
        tenant="adani-power",
        dedup_key="art-1",
        payload={"materiality": "CRITICAL", "title": "SEBI penalty",
                 "article_id": "art-1"},
    ))
    assert len(hints) == 1
    assert hints[0].severity == "high"
    assert "adani-power" in hints[0].headline


def test_risk_coach_silent_on_low_materiality():
    coach = RiskCoach()
    assert coach.evaluate(RiskArticleEvent(
        tenant="adani-power",
        dedup_key="art-2",
        payload={"materiality": "LOW", "title": "minor", "article_id": "art-2"},
    )) == []


# ---------- ForecastCoach --------------------------------------------------


def test_forecast_coach_fires_on_flip():
    coach = ForecastCoach()
    hints = coach.evaluate(ForecastShiftEvent(
        tenant="waaree-energies",
        dedup_key="fc-1",
        payload={"flip": "stable_to_declining", "horizon": "3m"},
    ))
    assert len(hints) == 1
    assert "declining" in hints[0].headline


def test_forecast_coach_silent_on_unknown_flip():
    coach = ForecastCoach()
    hints = coach.evaluate(ForecastShiftEvent(
        tenant="x",
        dedup_key="fc-2",
        payload={"flip": "still_stable", "horizon": "3m"},
    ))
    assert hints == []


def test_forecast_coach_severity_moderate_on_improving():
    coach = ForecastCoach()
    hints = coach.evaluate(ForecastShiftEvent(
        tenant="x",
        dedup_key="fc-3",
        payload={"flip": "declining_to_improving", "horizon": "6m"},
    ))
    assert len(hints) == 1
    assert hints[0].severity == "moderate"


# ---------- BeliefCoach -----------------------------------------------------


def test_belief_coach_fires_on_moderate_confidence():
    from engine.advisor.events import BeliefRevisionEvent
    from engine.advisor.personas import BeliefCoach

    coach = BeliefCoach()
    hints = coach.evaluate(BeliefRevisionEvent(
        tenant="adani-power",
        dedup_key="bc-1",
        payload={
            "belief_name": "water_risk_band",
            "confidence": "moderate",
            "rule_id": "R5",
            "new_value": "HIGH",
        },
    ))
    assert len(hints) == 1
    assert "moderate" == hints[0].severity
    assert "R5" in hints[0].body
    assert "water_risk_band" in hints[0].body


def test_belief_coach_silent_on_high_confidence():
    """High-confidence proposals auto-apply; coach shouldn't fire."""
    from engine.advisor.events import BeliefRevisionEvent
    from engine.advisor.personas import BeliefCoach

    coach = BeliefCoach()
    hints = coach.evaluate(BeliefRevisionEvent(
        tenant="x",
        dedup_key="bc-2",
        payload={"belief_name": "x", "confidence": "high", "rule_id": "R6"},
    ))
    assert hints == []


def test_belief_coach_low_confidence_is_high_severity():
    """Low-confidence proposals are most-suspect → high severity."""
    from engine.advisor.events import BeliefRevisionEvent
    from engine.advisor.personas import BeliefCoach

    coach = BeliefCoach()
    hints = coach.evaluate(BeliefRevisionEvent(
        tenant="x",
        dedup_key="bc-3",
        payload={"belief_name": "x", "confidence": "low", "rule_id": "R1"},
    ))
    assert hints[0].severity == "high"


# ---------- AutoresearcherCoach --------------------------------------------


def test_autoresearcher_coach_fires_on_top_hit():
    from engine.advisor.events import AutoresearcherKeepEvent
    from engine.advisor.personas import AutoresearcherCoach

    coach = AutoresearcherCoach()
    hints = coach.evaluate(AutoresearcherKeepEvent(
        dedup_key="ar-1",
        payload={
            "tier": "system",
            "knob_id": "materialFor:topic_climate:industry_power",
            "metric_delta": 0.034,
        },
    ))
    assert len(hints) == 1
    assert "+0.034" in hints[0].headline
    assert "system" in hints[0].headline


def test_autoresearcher_coach_silent_below_editorial_threshold():
    from engine.advisor.events import AutoresearcherKeepEvent
    from engine.advisor.personas import AutoresearcherCoach

    coach = AutoresearcherCoach()
    hints = coach.evaluate(AutoresearcherKeepEvent(
        dedup_key="ar-2",
        payload={"tier": "system", "knob_id": "k", "metric_delta": 0.005},
    ))
    assert hints == []


def test_autoresearcher_coach_ignores_other_events():
    from engine.advisor.events import DataIngestEvent
    from engine.advisor.personas import AutoresearcherCoach

    coach = AutoresearcherCoach()
    assert coach.evaluate(DataIngestEvent(payload={})) == []
