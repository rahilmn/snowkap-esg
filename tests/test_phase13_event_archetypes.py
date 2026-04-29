"""Phase 13 B1 — Event-archetype routing for recommendations.

Pre-fix every HOME-tier article got the same 5-rec template (file BRSR +
monitor + capex + assurance + operational hedging) regardless of event
type. Post-fix the LLM gets event-specific guidance via the archetype map
in `engine/analysis/recommendation_archetypes.py`.

These tests verify:
  1. The archetype map covers every event type in the ontology
     (preventing regressions when new events are added).
  2. Positive events route to upside-leveraging archetypes.
  3. Negative events route to remediation archetypes.
  4. The generator prompt includes the archetype block when an event_id
     is known, and explicitly tells the LLM not to default to compliance
     templates for positive events.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_archetype_map_covers_every_known_event_type() -> None:
    """Add a new event type to the ontology → must add an archetype list
    in recommendation_archetypes.py. This test fails loudly if a new
    event type appears without archetype coverage."""
    from engine.analysis.recommendation_archetypes import _ARCHETYPE_MAP

    expected_events = {
        # Positive
        "event_contract_win", "event_capacity_addition", "event_esg_certification",
        "event_order_book_update", "event_green_finance_milestone",
        "event_transition_announcement", "event_esg_partnership", "event_award_recognition",
        # Negative
        "event_supply_chain_disruption", "event_social_violation",
        "event_labour_strike", "event_cyber_incident", "event_community_protest",
        "event_ngo_report", "event_license_revocation",
        # Governance / regulatory
        "event_regulatory_policy", "event_board_change", "event_credit_rating",
        # Financial / routine
        "event_quarterly_results", "event_analyst_outlook", "event_dividend_policy",
        "event_ma_deal", "event_climate_disclosure_index",
    }
    missing = expected_events - set(_ARCHETYPE_MAP.keys())
    assert not missing, f"Event types missing from archetype map: {missing}"


def test_positive_event_archetypes_are_upside_focused() -> None:
    """Contract win, capacity addition, ESG certification → archetypes
    should mention investor / scaling / leverage, NOT regulator engagement."""
    from engine.analysis.recommendation_archetypes import get_archetypes_for_event

    for evt in ["event_contract_win", "event_capacity_addition", "event_esg_certification"]:
        archetypes = get_archetypes_for_event(evt)
        assert archetypes, f"No archetypes for {evt}"
        joined = " ".join(label.lower() for label, _ in archetypes)
        # Must include at least one upside lever
        assert any(w in joined for w in ["investor", "operational", "premium", "marketing", "ramp", "leverage"]), (
            f"{evt} archetypes don't mention upside leverage: {archetypes}"
        )
        # Must NOT default to remediation framing
        assert "remediate" not in joined
        assert "regulator engagement" not in joined


def test_negative_event_archetypes_are_remediation_focused() -> None:
    """Social violation / cyber incident / supply chain disruption → must
    include audit / remediation / disclosure archetypes."""
    from engine.analysis.recommendation_archetypes import get_archetypes_for_event

    for evt, must_have in [
        ("event_social_violation", "audit"),
        ("event_cyber_incident", "incident"),
        ("event_supply_chain_disruption", "sourcing"),
    ]:
        archetypes = get_archetypes_for_event(evt)
        assert archetypes, f"No archetypes for {evt}"
        joined = " ".join(label.lower() + " " + desc.lower() for label, desc in archetypes)
        assert must_have in joined, (
            f"{evt} archetypes missing expected term '{must_have}': {archetypes}"
        )


def test_unknown_event_returns_empty_list() -> None:
    """Unknown / fallback event_id → empty archetype list, generator
    falls through to legacy generic prompt (back-compat)."""
    from engine.analysis.recommendation_archetypes import get_archetypes_for_event

    assert get_archetypes_for_event("") == []
    assert get_archetypes_for_event("event_default") == []
    assert get_archetypes_for_event("event_made_up_xyz") == []


def test_is_positive_event_classification() -> None:
    """is_positive_event correctly identifies upside events."""
    from engine.analysis.recommendation_archetypes import is_positive_event

    assert is_positive_event("event_contract_win") is True
    assert is_positive_event("event_capacity_addition") is True
    assert is_positive_event("event_esg_certification") is True
    assert is_positive_event("event_green_finance_milestone") is True

    assert is_positive_event("event_social_violation") is False
    assert is_positive_event("event_cyber_incident") is False
    assert is_positive_event("event_supply_chain_disruption") is False
    assert is_positive_event("") is False
    assert is_positive_event("event_default") is False


def test_generator_prompt_includes_archetype_block_for_known_event() -> None:
    """The generator prompt MUST include EVENT-SPECIFIC GUIDANCE when an
    event_id is known. Otherwise the LLM falls back to the generic 5-rec
    template — exactly the bug we're fixing."""
    from engine.analysis.recommendation_engine import _build_generator_prompt
    from engine.analysis.insight_generator import DeepInsight

    insight = DeepInsight(
        headline="Waaree wins 500 MW solar auction",
        impact_score=6,
        core_mechanism="positive contract win",
        profitability_connection="₹477.5 Cr revenue",
        translation="growth signal",
        impact_analysis={},
        financial_timeline={},
        esg_relevance_score={},
        net_impact_summary="positive",
        decision_summary={"materiality": "MODERATE", "key_risk": "minor", "top_opportunity": "yes"},
        causal_chain={},
        warnings=[],
    )

    # Build a minimal PipelineResult-shaped mock
    result = MagicMock()
    result.title = "Waaree wins 500 MW solar auction"
    result.event = MagicMock(event_id="event_contract_win")
    result.themes = MagicMock(primary_theme="Energy")
    result.frameworks = []
    result.risk = None

    company = MagicMock()
    company.name = "Waaree Energies"
    company.industry = "Renewable Energy"
    company.market_cap = "Mid Cap"

    prompt = _build_generator_prompt(insight, result, company)
    assert "EVENT-SPECIFIC GUIDANCE" in prompt
    assert "event_contract_win" in prompt
    # Archetype labels must appear
    assert "Operational readiness" in prompt or "Investor communication" in prompt
    # Polarity warning must be present for positive events
    assert "POSITIVE event" in prompt
    assert "remediate" in prompt.lower() or "fabricated crisis" in prompt.lower()


def test_generator_prompt_no_polarity_warning_for_negative_event() -> None:
    """Negative events should NOT carry the 'this is a positive event'
    warning — that would mis-direct the LLM."""
    from engine.analysis.recommendation_engine import _build_generator_prompt
    from engine.analysis.insight_generator import DeepInsight

    insight = DeepInsight(
        headline="SEBI fines Adani Power ₹50 Cr",
        impact_score=8,
        core_mechanism="regulatory penalty",
        profitability_connection="₹50 Cr direct",
        translation="serious",
        impact_analysis={},
        financial_timeline={},
        esg_relevance_score={},
        net_impact_summary="negative",
        decision_summary={"materiality": "CRITICAL", "key_risk": "fines", "top_opportunity": ""},
        causal_chain={},
        warnings=[],
    )

    result = MagicMock()
    result.title = "SEBI fines Adani Power ₹50 Cr"
    result.event = MagicMock(event_id="event_regulatory_policy")
    result.themes = MagicMock(primary_theme="Compliance")
    result.frameworks = []
    result.risk = None

    company = MagicMock()
    company.name = "Adani Power"
    company.industry = "Power/Energy"
    company.market_cap = "Large Cap"

    prompt = _build_generator_prompt(insight, result, company)
    assert "EVENT-SPECIFIC GUIDANCE" in prompt
    assert "event_regulatory_policy" in prompt
    assert "POSITIVE event" not in prompt  # do NOT mis-flag negative events
    # Should include remediation-focused archetypes
    assert "Compliance-gap analysis" in prompt or "Regulator engagement" in prompt


def test_each_archetype_list_has_at_least_three_options() -> None:
    """Every event must have ≥ 3 archetypes so the LLM has variety to
    pick from. Fewer than 3 means the LLM might still produce repetitive
    recs."""
    from engine.analysis.recommendation_archetypes import _ARCHETYPE_MAP

    for evt, archetypes in _ARCHETYPE_MAP.items():
        assert len(archetypes) >= 3, (
            f"{evt} has only {len(archetypes)} archetypes; "
            f"need ≥ 3 for variety"
        )
        # Each archetype is a (label, desc) tuple of strings
        for arch in archetypes:
            assert isinstance(arch, tuple) and len(arch) == 2
            assert isinstance(arch[0], str) and isinstance(arch[1], str)
            assert arch[0] and arch[1]
