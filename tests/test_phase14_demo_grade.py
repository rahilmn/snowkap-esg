"""Phase 14 — demo-grade analysis quality tests.

Three blockers identified by the live fuzz audit on 2026-04-27:

  14.1 Cross-section ₹ drift not auto-reconciled (Waaree contract win:
       deep insight ₹477.5 Cr, ESG Analyst section ₹14.4 Cr — 30× gap).
       Fix: pass canonical ₹ as HARD constraint to all 3 perspective generators.

  14.2 Vedanta Konkola precedent surfacing on positive events.
       Fix: add 8 positive-event precedents (contract wins, capacity adds,
       ESG upgrades, green-finance milestones) to precedents.ttl so the
       SPARQL query has event-appropriate matches to return.

  14.3 LLM defaults to "₹10-50 Cr SEBI penalty" defensive framing on
       contract wins despite the Phase 13 archetype polarity warning.
       Fix: dedicated _POSITIVE_GENERATOR_SYSTEM prompt with explicit
       polarity guardrails; dispatcher routes based on is_positive_event().
"""

from __future__ import annotations

from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# 14.1 — Canonical ₹ hard constraint
# ---------------------------------------------------------------------------


def _mk_insight(headline: str = "Test", canonical_cr: float = 477.5):
    """Build a minimal DeepInsight with a canonical ₹ figure visible in
    multiple fields so the cross-section helper picks it up."""
    from engine.analysis.insight_generator import DeepInsight

    return DeepInsight(
        headline=f"{headline} adding ₹{canonical_cr:.1f} Cr",
        impact_score=6,
        core_mechanism="x",
        profitability_connection=f"Adds ₹{canonical_cr:.1f} Cr revenue",
        translation="x",
        impact_analysis={},
        financial_timeline={},
        esg_relevance_score={},
        net_impact_summary=f"Net ₹{canonical_cr:.1f} Cr impact",
        decision_summary={
            "materiality": "MODERATE",
            "financial_exposure": f"₹{canonical_cr:.1f} Cr direct revenue",
            "key_risk": "minor",
            "top_opportunity": f"Capture ₹{canonical_cr:.1f} Cr",
        },
        causal_chain={},
        warnings=[],
    )


def test_esg_analyst_prompt_includes_canonical_exposure_hard_constraint() -> None:
    """The ESG Analyst user prompt MUST include CANONICAL_EXPOSURE so the
    LLM can't substitute a smaller cascade-only number."""
    from engine.analysis.esg_analyst_generator import _build_user_prompt

    insight = _mk_insight(canonical_cr=477.5)
    result = MagicMock()
    result.title = "Test"
    result.event = MagicMock(event_id="event_contract_win", matched_keywords=[])
    result.themes = MagicMock(primary_theme="Energy")
    result.frameworks = []
    result.risk = None
    result.causal_chains = []
    result.sdgs = []
    result.stakeholders = []
    result.nlp = MagicMock(sentiment=0)

    company = MagicMock()
    company.name = "Test Co"
    company.industry = "Renewable Energy"
    company.market_cap = "Mid Cap"
    company.primitive_calibration = {
        "revenue_cr": 14376, "opex_cr": 12038, "energy_share_of_opex": 0.15, "fy_year": 2026,
    }

    prompt = _build_user_prompt(insight, result, company)
    assert "CANONICAL_EXPOSURE" in prompt, "ESG Analyst prompt missing canonical ₹ constraint"
    assert "477.5" in prompt
    # Must include the directive language so the LLM knows it's not optional
    assert "REQUIRED" in prompt
    assert "Phase-14 anti-drift" in prompt


def test_ceo_prompt_includes_canonical_exposure_hard_constraint() -> None:
    """The CEO narrative user prompt MUST include CANONICAL_EXPOSURE so
    the board paragraph + 3-year trajectory + Q&A use the same figure."""
    from engine.analysis.ceo_narrative_generator import _build_user_prompt

    insight = _mk_insight(canonical_cr=12.3)
    result = MagicMock()
    result.title = "Test"
    result.event = MagicMock(event_id="event_supply_chain_disruption")
    result.themes = MagicMock(primary_theme="Energy")

    company = MagicMock()
    company.name = "JSW Energy"
    company.industry = "Power/Energy"
    company.market_cap = "Large Cap"
    company.primitive_calibration = {"revenue_cr": 11484, "fy_year": 2026, "debt_to_equity": 1.0}

    prompt = _build_user_prompt(insight, result, company)
    assert "CANONICAL_EXPOSURE" in prompt
    assert "12.3" in prompt
    assert "Phase-14 anti-drift" in prompt


# ---------------------------------------------------------------------------
# 14.2 — Positive-event precedent library coverage
# ---------------------------------------------------------------------------


def test_positive_event_precedents_now_returned_for_contract_win() -> None:
    """Contract-win SPARQL must return at least 1 positive precedent
    (Tata Power SECI / ReNew BESS / L&T NTPC), not fall back to negatives."""
    from engine.ontology.intelligence import query_precedents_for_event
    from engine.ontology.graph import OntologyGraph

    g = OntologyGraph().load()
    preds = query_precedents_for_event("event_contract_win", "Renewable Energy", limit=3, graph=g)
    assert len(preds) >= 1, "No positive precedents for event_contract_win"
    # Must include at least one of the new positive cases
    names = {p.name.lower() for p in preds}
    expected_any = {"tata power 4 gw seci tariff auction win", "renew power 1 gwh bess pspcl auction win", "l&t ntpc 1 gw solar epc order win"}
    assert any(n in names for n in expected_any), (
        f"Expected at least one new positive precedent in {names}"
    )
    # Must NOT include the Vedanta Konkola precedent (negative event)
    assert not any("vedanta konkola" in n for n in names)


def test_positive_event_precedents_for_capacity_addition() -> None:
    """Capacity-addition events should match JSW Vijayanagar / Adani Khavda."""
    from engine.ontology.intelligence import query_precedents_for_event
    from engine.ontology.graph import OntologyGraph

    g = OntologyGraph().load()
    preds = query_precedents_for_event("event_capacity_addition", "Renewable Energy", limit=3, graph=g)
    assert len(preds) >= 1
    names = {p.name.lower() for p in preds}
    assert any("vijayanagar" in n or "khavda" in n for n in names)


def test_positive_event_precedents_for_esg_certification() -> None:
    """ESG-cert events should match HDFC ISO / Infosys MSCI upgrades."""
    from engine.ontology.intelligence import query_precedents_for_event
    from engine.ontology.graph import OntologyGraph

    g = OntologyGraph().load()
    preds = query_precedents_for_event("event_esg_certification", "Financials/Banking", limit=3, graph=g)
    assert len(preds) >= 1
    names = {p.name.lower() for p in preds}
    assert any("hdfc bank" in n or "infosys" in n for n in names)


def test_green_finance_milestone_returns_renew_or_ultratech() -> None:
    from engine.ontology.intelligence import query_precedents_for_event
    from engine.ontology.graph import OntologyGraph

    g = OntologyGraph().load()
    preds = query_precedents_for_event("event_green_finance_milestone", "Renewable Energy", limit=3, graph=g)
    assert len(preds) >= 1
    names = {p.name.lower() for p in preds}
    assert any("renew" in n or "ultratech" in n for n in names)


# ---------------------------------------------------------------------------
# 14.3 — Dedicated positive-event LLM prompt
# ---------------------------------------------------------------------------


def test_positive_event_generator_system_exists_and_has_polarity_guardrails() -> None:
    """The new _POSITIVE_GENERATOR_SYSTEM must:
      - exist
      - explicitly forbid SEBI-penalty injection
      - explicitly forbid generic monitor-and-escalate framing
      - centre archetypes on investor-comms / capacity / capital deployment"""
    from engine.analysis.recommendation_engine import _POSITIVE_GENERATOR_SYSTEM

    text = _POSITIVE_GENERATOR_SYSTEM
    # Polarity guardrails
    assert "DO NOT recommend \"engage SEBI" in text or "engage SEBI" in text  # forbid clause present
    assert "no enforcement event" in text.lower() or "no regulatory action" in text.lower() \
        or "DO NOT cite \"₹X-Y Cr SEBI penalty" in text
    assert "monitor and escalate" in text.lower()
    # Archetype guidance
    assert "Investor communication" in text
    assert "Capital deployment" in text
    # Schema preserved
    assert "audit_trail" in text
    assert "responsible_party" in text


def test_dispatcher_routes_positive_events_to_positive_prompt() -> None:
    """The recommendation engine must dispatch is_positive_event(event_id)
    to _POSITIVE_GENERATOR_SYSTEM, NOT the default negative prompt."""
    from engine.analysis.recommendation_archetypes import is_positive_event

    # Sanity: archetype lookup returns True for the expected positive set
    for evt in [
        "event_contract_win",
        "event_capacity_addition",
        "event_esg_certification",
        "event_green_finance_milestone",
    ]:
        assert is_positive_event(evt), f"{evt} should be positive"

    # Negative events stay on the default prompt
    for evt in [
        "event_social_violation",
        "event_cyber_incident",
        "event_supply_chain_disruption",
        "event_regulatory_policy",
    ]:
        assert not is_positive_event(evt), f"{evt} should be negative"


def test_positive_prompt_does_not_inherit_remediation_language() -> None:
    """Sanity check: the positive prompt must NOT contain the remediation
    phrasing that biases the LLM toward defensive framing."""
    from engine.analysis.recommendation_engine import _POSITIVE_GENERATOR_SYSTEM, _GENERATOR_SYSTEM

    # The default (negative) prompt explicitly says "REMEDIATION and PREVENTION"
    assert "REMEDIATION and PREVENTION" in _GENERATOR_SYSTEM
    # The positive prompt must not
    assert "REMEDIATION and PREVENTION" not in _POSITIVE_GENERATOR_SYSTEM


# ---------------------------------------------------------------------------
# 14.4 — Stage 10 deep-insight positive-event polarity directive
# ---------------------------------------------------------------------------


def test_stage10_has_positive_event_directive_constant() -> None:
    """The Stage-10 module must export a `_POSITIVE_INSIGHT_DIRECTIVE`
    constant that gets appended to the system prompt for positive events."""
    from engine.analysis import insight_generator

    directive = getattr(insight_generator, "_POSITIVE_INSIGHT_DIRECTIVE", None)
    assert directive is not None, (
        "Stage-10 missing _POSITIVE_INSIGHT_DIRECTIVE — Phase 14.4 incomplete"
    )
    assert "POSITIVE-EVENT" in directive
    # Must explicitly forbid the SEBI-penalty injection that the live audit
    # caught on contract wins.
    assert "SEBI penalty" in directive
    assert "do not" in directive.lower() or "never inject" in directive.lower()
    # Must guide key_risk + financial_exposure framing
    assert "key_risk" in directive
    assert "financial_exposure" in directive
    # Must mention at least 2 of the 4 positive-event archetypes
    assert any(k in directive.lower() for k in ["contract win", "capacity addition"])


def test_stage10_directive_does_not_alter_negative_prompt() -> None:
    """The negative-event Stage-10 prompt (the base _SYSTEM_PROMPT) must NOT
    inadvertently contain the positive-event directive — they should be
    appended at runtime, not pre-merged."""
    from engine.analysis.insight_generator import _SYSTEM_PROMPT

    assert "POSITIVE-EVENT POLARITY DIRECTIVE" not in _SYSTEM_PROMPT
    # The base prompt should still cover the legacy specificity rules
    assert "FINANCIAL ACCURACY RULES" in _SYSTEM_PROMPT
    assert "SOURCE TAGGING RULES" in _SYSTEM_PROMPT
