"""L7 — Belief revision rule tests (deterministic skeleton).

Locks the 4 rule contracts (R1–R4) so LLM refinement later can replace
the rule body but not the interface.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.audit import append_decision, make_toulmin
from engine.governance.belief_revision import BeliefProposal, revise_from_article
from engine.governance.belief_schema import (
    FinancialExposureBelief,
    RiskBandBelief,
    TransitionStanceBelief,
)
from engine.governance.company_agent import CompanyAgent


# ---------------------------------------------------------------------------
# R1 — negative + HIGH materiality → RiskBandBelief
# ---------------------------------------------------------------------------


def test_r1_fires_on_negative_high_materiality():
    article = {
        "id": "art_1",
        "event_id": "event_regulatory_penalty",
        "event_polarity": "negative",
        "materiality": "HIGH",
        "topic": "climate",
    }
    out = revise_from_article(article=article)
    assert len(out) == 1
    assert out[0].rule_id == "R1"
    assert isinstance(out[0].belief, RiskBandBelief)
    assert out[0].belief.band == "HIGH"
    assert out[0].belief.topic == "climate"


def test_r1_uses_critical_band_for_critical_materiality():
    article = {
        "id": "art_2", "event_id": "x", "event_polarity": "negative",
        "materiality": "CRITICAL", "topic": "labour",
    }
    out = revise_from_article(article=article)
    assert out[0].belief.band == "CRITICAL"


def test_r1_no_fire_on_positive_event():
    article = {
        "id": "art_3", "event_id": "x", "event_polarity": "positive",
        "materiality": "HIGH", "topic": "climate",
    }
    out = revise_from_article(article=article)
    # R1 won't fire (positive); R3 won't fire (event_id isn't in transition set)
    assert out == []


def test_r1_no_fire_on_low_materiality():
    article = {
        "id": "art_4", "event_id": "x", "event_polarity": "negative",
        "materiality": "LOW", "topic": "climate",
    }
    assert revise_from_article(article=article) == []


# ---------------------------------------------------------------------------
# R2 — cascade ≥ 5% of revenue → FinancialExposureBelief
# ---------------------------------------------------------------------------


def test_r2_fires_when_cascade_exceeds_five_percent():
    article = {"id": "a", "event_id": "ev", "event_polarity": "neutral", "materiality": "LOW"}
    cascade = {"total_cr": 600.0, "method": "cascade"}
    # 5% of 10,000 = 500 < 600 → fires
    out = revise_from_article(
        article=article, cascade_result=cascade, company_revenue_cr=10000,
    )
    assert any(p.rule_id == "R2" for p in out)
    fexp = next(p for p in out if p.rule_id == "R2")
    assert isinstance(fexp.belief, FinancialExposureBelief)
    assert fexp.belief.exposure_cr_lo == pytest.approx(480.0)   # 0.8 × 600
    assert fexp.belief.exposure_cr_hi == pytest.approx(720.0)   # 1.2 × 600


def test_r2_no_fire_below_threshold():
    article = {"id": "a", "event_id": "ev", "event_polarity": "neutral", "materiality": "LOW"}
    cascade = {"total_cr": 400.0, "method": "cascade"}
    # 5% of 10,000 = 500 > 400 → no fire
    out = revise_from_article(
        article=article, cascade_result=cascade, company_revenue_cr=10000,
    )
    assert [p for p in out if p.rule_id == "R2"] == []


def test_r2_no_fire_when_revenue_unknown():
    article = {"id": "a", "event_id": "ev", "event_polarity": "neutral", "materiality": "LOW"}
    cascade = {"total_cr": 9999.0, "method": "cascade"}
    out = revise_from_article(article=article, cascade_result=cascade, company_revenue_cr=0)
    assert out == []


# ---------------------------------------------------------------------------
# R3 — positive transition event → TransitionStanceBelief
# ---------------------------------------------------------------------------


def test_r3_fires_on_transition_announcement():
    article = {
        "id": "a", "event_id": "event_transition_announcement",
        "event_polarity": "positive", "materiality": "MODERATE",
    }
    out = revise_from_article(article=article)
    assert any(p.rule_id == "R3" for p in out)
    p = next(x for x in out if x.rule_id == "R3")
    assert isinstance(p.belief, TransitionStanceBelief)
    assert p.belief.stance == "fast_follower"


def test_r3_does_not_fire_on_negative_transition_event():
    """Negative-polarity transition events shouldn't propose `fast_follower`."""
    article = {
        "id": "a", "event_id": "event_transition_announcement",
        "event_polarity": "negative", "materiality": "HIGH",
        "topic": "climate",
    }
    out = revise_from_article(article=article)
    # R1 fires (negative + HIGH + topic), R3 does not
    rules = {p.rule_id for p in out}
    assert "R1" in rules
    assert "R3" not in rules


# ---------------------------------------------------------------------------
# R4 — recent high-uncertainty events downshift confidence
# ---------------------------------------------------------------------------


def test_r4_downshifts_confidence_when_recent_high_uncertainty():
    article = {
        "id": "a", "event_id": "x", "event_polarity": "negative",
        "materiality": "HIGH", "topic": "climate",
    }
    now = datetime.now(timezone.utc)
    advisor_events = [
        {
            "ts": (now - timedelta(days=2)).isoformat(timespec="seconds"),
            "event_type": "high_uncertainty_decision",
        },
    ]
    out = revise_from_article(article=article, advisor_events=advisor_events)
    # R1 fires; R4 downshifts moderate → low
    p = out[0]
    assert p.belief.confidence_band == "low"
    assert "R4" in p.rationale


def test_r4_ignores_old_advisor_events():
    article = {
        "id": "a", "event_id": "x", "event_polarity": "negative",
        "materiality": "HIGH", "topic": "climate",
    }
    now = datetime.now(timezone.utc)
    advisor_events = [
        {
            "ts": (now - timedelta(days=30)).isoformat(timespec="seconds"),
            "event_type": "high_uncertainty_decision",
        },
    ]
    out = revise_from_article(article=article, advisor_events=advisor_events)
    p = out[0]
    # No downshift because event is > 7d old
    assert p.belief.confidence_band == "moderate"
    assert "R4" not in p.rationale


# ---------------------------------------------------------------------------
# R5 — forecaster-driven proposal
# ---------------------------------------------------------------------------


def test_r5_fires_when_forecaster_declines_3m_and_6m():
    """Two consecutive declining horizons at moderate+ confidence → R5
    proposes a HIGH risk-band on the article's topic."""
    article = {
        "id": "a", "event_id": "x", "event_polarity": "neutral",
        "materiality": "LOW", "topic": "climate",
    }
    forecaster = {
        "company_slug": "adani-power",
        "horizons": {
            "3m": {"direction": "declining", "confidence": "moderate", "rationale": "test"},
            "6m": {"direction": "declining", "confidence": "moderate", "rationale": "test"},
            "12m": {"direction": "stable", "confidence": "low", "rationale": "test"},
        },
    }
    out = revise_from_article(article=article, forecaster_output=forecaster)
    r5 = [p for p in out if p.rule_id == "R5"]
    assert len(r5) == 1
    assert isinstance(r5[0].belief, RiskBandBelief)
    assert r5[0].belief.topic == "climate"
    assert r5[0].belief.band == "HIGH"


def test_r5_does_not_double_count_with_r1():
    """When R1 already proposed a risk-band for the same topic, R5
    must NOT add a duplicate."""
    article = {
        "id": "a", "event_id": "x", "event_polarity": "negative",
        "materiality": "HIGH", "topic": "climate",
    }
    forecaster = {
        "company_slug": "adani-power",
        "horizons": {
            "3m": {"direction": "declining", "confidence": "moderate", "rationale": "test"},
            "6m": {"direction": "declining", "confidence": "moderate", "rationale": "test"},
            "12m": {"direction": "stable", "confidence": "low", "rationale": "test"},
        },
    }
    out = revise_from_article(article=article, forecaster_output=forecaster)
    # R1 fires (negative + HIGH + topic) — R5 must NOT also propose
    rules = [p.rule_id for p in out]
    assert "R1" in rules
    assert "R5" not in rules


def test_r5_no_fire_on_single_declining_horizon():
    """Only ONE horizon declining is not enough; R5 needs both 3m + 6m."""
    article = {
        "id": "a", "event_id": "x", "event_polarity": "neutral",
        "materiality": "LOW", "topic": "climate",
    }
    forecaster = {
        "horizons": {
            "3m": {"direction": "declining", "confidence": "moderate", "rationale": "x"},
            "6m": {"direction": "stable", "confidence": "moderate", "rationale": "x"},
            "12m": {"direction": "stable", "confidence": "low", "rationale": "x"},
        },
    }
    out = revise_from_article(article=article, forecaster_output=forecaster)
    assert [p for p in out if p.rule_id == "R5"] == []


def test_r5_no_fire_on_low_confidence_forecast():
    """Even if both horizons decline, low confidence on either kills R5."""
    article = {
        "id": "a", "event_id": "x", "event_polarity": "neutral",
        "materiality": "LOW", "topic": "climate",
    }
    forecaster = {
        "horizons": {
            "3m": {"direction": "declining", "confidence": "low", "rationale": "x"},
            "6m": {"direction": "declining", "confidence": "low", "rationale": "x"},
            "12m": {"direction": "stable", "confidence": "low", "rationale": "x"},
        },
    }
    out = revise_from_article(article=article, forecaster_output=forecaster)
    assert [p for p in out if p.rule_id == "R5"] == []


def test_r5_no_fire_when_topic_missing():
    """R5 needs a topic to propose against — no topic → no proposal."""
    article = {"id": "a", "event_id": "x", "event_polarity": "neutral", "materiality": "LOW"}
    forecaster = {
        "horizons": {
            "3m": {"direction": "declining", "confidence": "moderate", "rationale": "x"},
            "6m": {"direction": "declining", "confidence": "moderate", "rationale": "x"},
            "12m": {"direction": "stable", "confidence": "low", "rationale": "x"},
        },
    }
    out = revise_from_article(article=article, forecaster_output=forecaster)
    assert [p for p in out if p.rule_id == "R5"] == []


def test_company_agent_threads_forecaster_through(tmp_path):
    """CompanyAgent.revise_from_article passes forecaster_output to the
    underlying revise call."""
    agent = CompanyAgent(tenant="adani-power", audit_dir=tmp_path, auto_persist=False)
    forecaster = {
        "company_slug": "adani-power",
        "horizons": {
            "3m": {"direction": "declining", "confidence": "moderate", "rationale": "x"},
            "6m": {"direction": "declining", "confidence": "moderate", "rationale": "x"},
            "12m": {"direction": "stable", "confidence": "low", "rationale": "x"},
        },
    }
    article = {
        "id": "a", "event_id": "x", "event_polarity": "neutral",
        "materiality": "LOW", "topic": "labour",
    }
    proposals = agent.revise_from_article(
        article=article, forecaster_output=forecaster, apply=False,
    )
    r5 = [p for p in proposals if p.rule_id == "R5"]
    assert len(r5) == 1
    assert r5[0].belief.topic == "labour"


# ---------------------------------------------------------------------------
# R6 — autoresearcher proposal (Tier 1+ wiring)
# ---------------------------------------------------------------------------


def test_r6_fires_on_autoresearcher_proposal_above_threshold():
    """When the autoresearcher promotes a knob with metric_delta above
    the keep threshold, R6 surfaces a BeliefProposal for the article's
    topic."""
    article = {
        "id": "a", "event_id": "x", "event_polarity": "neutral",
        "materiality": "LOW", "topic": "climate",
    }
    proposal = {
        "knob_kind": "ontology_weight",
        "knob_id": "materialFor:topic_climate:industry_power",
        "metric_delta": 0.05,
        "keep_threshold": 0.02,
    }
    out = revise_from_article(article=article, autoresearcher_proposal=proposal)
    r6 = [p for p in out if p.rule_id == "R6"]
    assert len(r6) == 1
    assert isinstance(r6[0].belief, RiskBandBelief)
    assert r6[0].belief.topic == "climate"


def test_r6_does_not_fire_below_threshold():
    article = {
        "id": "a", "event_id": "x", "event_polarity": "neutral",
        "materiality": "LOW", "topic": "climate",
    }
    proposal = {
        "knob_kind": "ontology_weight",
        "knob_id": "test",
        "metric_delta": 0.01,  # below threshold
        "keep_threshold": 0.02,
    }
    out = revise_from_article(article=article, autoresearcher_proposal=proposal)
    assert [p for p in out if p.rule_id == "R6"] == []


def test_r6_no_fire_when_topic_missing():
    article = {"id": "a", "event_id": "x", "event_polarity": "neutral", "materiality": "LOW"}
    proposal = {"knob_id": "k", "metric_delta": 0.10, "keep_threshold": 0.02}
    out = revise_from_article(article=article, autoresearcher_proposal=proposal)
    assert [p for p in out if p.rule_id == "R6"] == []


def test_r6_no_fire_when_proposal_missing():
    """Default behaviour: no autoresearcher_proposal → no R6 (Tier 0 path)."""
    article = {
        "id": "a", "event_id": "x", "event_polarity": "neutral",
        "materiality": "LOW", "topic": "climate",
    }
    out = revise_from_article(article=article)
    assert [p for p in out if p.rule_id == "R6"] == []


# ---------------------------------------------------------------------------
# LLM callback hook
# ---------------------------------------------------------------------------


def test_llm_callback_replaces_deterministic_proposals():
    """L7 — LLM callback gets the deterministic proposals + context;
    its return list is used in place of the deterministic output."""
    article = {
        "id": "a", "event_id": "x", "event_polarity": "negative",
        "materiality": "HIGH", "topic": "climate",
    }

    def callback(proposals, context):
        # Override: drop everything, emit a single MODERATE band
        return [BeliefProposal(
            belief=RiskBandBelief(topic="llm-override", band="MODERATE"),
            rationale="LLM said so",
            rule_id="LLM",
        )]

    out = revise_from_article(article=article, llm_callback=callback)
    assert len(out) == 1
    assert out[0].rule_id == "LLM"
    assert out[0].belief.topic == "llm-override"


def test_llm_callback_failure_falls_back_to_deterministic():
    """L7 — LLM exception MUST NOT lose the deterministic baseline."""
    article = {
        "id": "a", "event_id": "x", "event_polarity": "negative",
        "materiality": "HIGH", "topic": "climate",
    }

    def boom(proposals, context):
        raise RuntimeError("LLM unavailable")

    out = revise_from_article(article=article, llm_callback=boom)
    # Deterministic R1 should still be present
    assert len(out) == 1
    assert out[0].rule_id == "R1"


# ---------------------------------------------------------------------------
# CompanyAgent.revise_from_article (apply=False vs apply=True)
# ---------------------------------------------------------------------------


def test_agent_revise_apply_false_does_not_mutate_state(tmp_path):
    agent = CompanyAgent(tenant="adani-power", audit_dir=tmp_path, auto_persist=False)
    article = {
        "id": "a", "event_id": "ev", "event_polarity": "negative",
        "materiality": "HIGH", "topic": "climate",
    }
    proposals = agent.revise_from_article(article=article, apply=False)
    assert len(proposals) == 1
    assert agent.beliefs == {}


def test_agent_revise_apply_true_commits_proposals(tmp_path):
    agent = CompanyAgent(tenant="adani-power", audit_dir=tmp_path, auto_persist=False)
    article = {
        "id": "a", "event_id": "ev", "event_polarity": "negative",
        "materiality": "HIGH", "topic": "climate",
    }
    proposals = agent.revise_from_article(article=article, apply=True)
    assert len(proposals) == 1
    # Belief committed under the canonical kind:discriminator name
    assert "risk_band:climate" in agent.beliefs
    assert agent.beliefs["risk_band:climate"].value["band"] == "HIGH"


def test_agent_revise_picks_up_own_advisor_events(tmp_path):
    """L7 — the agent reads its OWN advisor queue when revising, so a
    high-uncertainty entry on the same tenant downshifts confidence."""
    # Emit a high-uncertainty advisor event for the tenant
    append_decision(
        "materiality_downgrade",
        article_id="prior_art",
        company_slug="adani-power",
        toulmin=make_toulmin("x", ["y"], "z", qualifier="q"),
        tags={
            "scope": "tenant",
            "signal_type": "analyst_judgment",
            "attribution": "criticality_scorer",
            "uncertainty": "high",
        },
        base_data_dir=tmp_path,
    )

    agent = CompanyAgent(tenant="adani-power", audit_dir=tmp_path, auto_persist=False)
    article = {
        "id": "a", "event_id": "ev", "event_polarity": "negative",
        "materiality": "HIGH", "topic": "climate",
    }
    proposals = agent.revise_from_article(article=article, apply=False)
    # R1 fires, R4 picks up the advisor event and downshifts to low
    assert proposals[0].belief.confidence_band == "low"
    assert "R4" in proposals[0].rationale
