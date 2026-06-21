"""Phase 53 (D) — connect the industry-thematic lane to company intelligence.

Once a SECTOR ESG article (company NOT named) ranks into the critical tier
(Phase 53.C), the downstream intelligence must treat it correctly so it actually
reaches the reader WITH recommendations — the core product promise ("any
onboarded company gets 3 critical + recs"). Three gates would otherwise silently
drop it:

  1. Approval gate — rejects any critical whose company-facts are "absent from
     the source". For a thematic article the company is absent BY DESIGN, so the
     reviewer is given a sector-article reframe (event facts still required to be
     grounded; only the application to the company is treated as inference).
  2. Stage 10 insight — must frame the company's EXPOSURE to a sector
     development, not assert the company itself acted (keeps it grounded).
  3. Rec engine MONITOR-suppress — a thematic ESG event must be actionable so
     recs are generated, not suppressed to monitor-only.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from engine.analysis.approval_gate import (
    _build_review_prompt,
    _is_thematic,
    _thematic_review_note,
)


# ---------------------------------------------------------------------------
# 1. Approval gate — sector-article reframe
# ---------------------------------------------------------------------------

def _result(source_type, body="RBI issued new climate disclosure norms for all banks.",
            title="RBI tightens climate norms for banks"):
    return SimpleNamespace(source_type=source_type, article_content=body, title=title, article_id="a")


_ANALYSIS = {
    "why_it_matters": {"criticality_summary": "Material to the bank's climate exposure."},
    "what_changed": {"headline": "RBI climate norms"},
    "lede": {"text": "The RBI has tightened climate disclosure norms for banks."},
}


def test_is_thematic_flag():
    assert _is_thematic(_result("industry_thematic")) is True
    assert _is_thematic(_result("")) is False
    assert _is_thematic(_result("newsapi_ai")) is False


def test_thematic_note_names_company_and_permits_absence():
    note = _thematic_review_note(SimpleNamespace(name="ICICI Bank"))
    assert "ICICI Bank" in note
    assert "NOT named" in note
    # must still demand the event facts be grounded
    assert "grounded in the article" in note


def test_review_prompt_injects_note_only_for_thematic():
    co = SimpleNamespace(name="ICICI Bank")
    p_them = _build_review_prompt(_result("industry_thematic"), _ANALYSIS, None, co)
    p_named = _build_review_prompt(_result(""), _ANALYSIS, None, co)
    assert "SECTOR / INDUSTRY ARTICLE" in p_them and "ICICI Bank" in p_them
    assert "SECTOR / INDUSTRY ARTICLE" not in p_named
    # the source body is still present in both (grounding preserved)
    assert "climate disclosure norms" in p_them


def test_review_prompt_handles_missing_company_name():
    # never crash if company is None / nameless
    p = _build_review_prompt(_result("industry_thematic"), _ANALYSIS, None, None)
    assert "this company" in p


# ---------------------------------------------------------------------------
# 2. Stage 10 insight — sector-exposure framing
# ---------------------------------------------------------------------------

def _insight_result(source_type):
    r = MagicMock()
    r.title = "RBI tightens climate norms for banks"
    r.source = "Mint"
    r.published_at = "2026-06-18T00:00:00Z"
    r.url = "https://x.test/rbi"
    r.article_content = (
        "The Reserve Bank of India issued new climate-risk disclosure norms applicable "
        "to all scheduled commercial banks, requiring financed-emissions reporting." * 4
    )
    r.source_type = source_type
    r.frameworks = []
    r.risk = None
    r.causal_chains = []
    r.sdgs = []
    r.stakeholders = []
    nlp = MagicMock()
    nlp.sentiment = -1
    nlp.tone = ["regulatory"]
    nlp.source_credibility_tier = 1
    nlp.narrative_core_claim = "RBI tightens climate norms"
    nlp.narrative_implied_causation = "norms -> compliance cost"
    nlp.narrative_stakeholder_framing = "regulator vs banks"
    nlp.entities = ["RBI", "banks"]
    nlp.financial_signal = {}
    nlp.regulatory_references = ["RBI"]
    r.nlp = nlp
    r.themes = MagicMock(primary_theme="Climate Change")
    r.relevance = MagicMock(total=4, tier="SECONDARY", materiality_weight=0.95)
    r.event = MagicMock(event_id="event_climate_event", matched_keywords=["climate"], score_floor=5)
    return r


def _company():
    c = MagicMock()
    c.name = "ICICI Bank"
    c.industry = "Financials/Banking"
    c.sasb_category = "Commercial Banks"
    c.market_cap = "Large Cap"
    c.headquarter_city = "Mumbai"
    c.headquarter_country = "India"
    c.headquarter_region = "South Asia"
    c.slug = "icici-bank"
    return c


def test_insight_prompt_adds_sector_framing_for_thematic():
    from engine.analysis.insight_generator import _build_user_prompt
    p = _build_user_prompt(_insight_result("industry_thematic"), _company())
    assert "SECTOR-EXPOSURE FRAMING" in p
    assert "NOT named" in p
    # must instruct attribution away from the company
    assert "EXPOSURE" in p


def test_insight_prompt_omits_sector_framing_for_company_named():
    from engine.analysis.insight_generator import _build_user_prompt
    p = _build_user_prompt(_insight_result(""), _company())
    assert "SECTOR-EXPOSURE FRAMING" not in p


# ---------------------------------------------------------------------------
# 3. Rec engine — thematic ESG event is NOT suppressed to monitor-only
# ---------------------------------------------------------------------------

def _insight(materiality="CRITICAL", action="MONITOR", impact=6):
    from engine.analysis.insight_generator import DeepInsight
    return DeepInsight(
        headline="RBI climate norms", impact_score=impact, core_mechanism="x",
        profitability_connection="x", translation="x", impact_analysis={},
        financial_timeline={}, esg_relevance_score={}, net_impact_summary="x",
        decision_summary={"materiality": materiality, "action": action},
        causal_chain={}, warnings=[],
    )


def test_thematic_climate_event_not_suppressed():
    from engine.analysis.recommendation_engine import _should_skip
    # event_climate_event is now actionable (Phase 53.C) → MONITOR gate must NOT fire.
    r = MagicMock()
    r.event = MagicMock(event_id="event_climate_event")
    r.title = "RBI tightens climate norms for banks"
    skip, reason = _should_skip(_insight(action="MONITOR"), r)
    assert skip is False, f"thematic ESG event wrongly suppressed: {reason}"


def test_non_actionable_monitor_still_suppressed():
    from engine.analysis.recommendation_engine import _should_skip
    r = MagicMock()
    r.event = MagicMock(event_id="event_analyst_outlook")
    r.title = "Which bank is a better bet"
    skip, reason = _should_skip(_insight(materiality="LOW", action="MONITOR"), r)
    assert skip is True
