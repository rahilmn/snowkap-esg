"""Phase 15 — Stakeholder-position polarity tests.

Closes the residual issue from the Phase 14 audit: even after Phase 14.2
fixed the analogous_precedent for positive events, the per-stakeholder
precedent strings in the CEO stakeholder map STILL cited "Vedanta 2020
SCN" and "Wells Fargo 2016 BBB→B over fraud" because those strings were
hardcoded in stakeholder_positions.ttl.

Phase 15 added:
  - 2 new optional predicates (stakeholderPositiveStance,
    stakeholderPositivePrecedent) on every StakeholderPosition entry
  - 9 stakeholders now have positive-flavour variants
  - query_stakeholder_positions(event_polarity="positive") routes to the
    positive variants
  - CEO narrative generator dispatcher passes is_positive_event → polarity
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def graph():
    from engine.ontology.graph import OntologyGraph
    return OntologyGraph().load()


# ---------------------------------------------------------------------------
# 15.2 — Positive variants exist on every stakeholder
# ---------------------------------------------------------------------------


def test_every_stakeholder_has_positive_variant(graph) -> None:
    """All 9 StakeholderPosition entries must carry positive-flavour stance
    + precedent. If a new stakeholder is added without these predicates,
    this test fails loudly so we don't silently regress to negative-only
    output on positive events."""
    sparql = """
    SELECT ?pos ?label ?pos_stance ?pos_prec WHERE {
        ?pos a snowkap:StakeholderPosition ;
             snowkap:stakeholderLabel ?label .
        OPTIONAL { ?pos snowkap:stakeholderPositiveStance ?pos_stance }
        OPTIONAL { ?pos snowkap:stakeholderPositivePrecedent ?pos_prec }
    }
    """
    rows = graph.select_rows(sparql)
    missing = []
    for row in rows:
        if not row.get("pos_stance") or not row.get("pos_prec"):
            missing.append(str(row.get("label", "")))
    assert not missing, (
        f"Stakeholders missing positive variants: {missing}. "
        f"Phase 15 requires every entry to carry stakeholderPositiveStance + "
        f"stakeholderPositivePrecedent."
    )


# ---------------------------------------------------------------------------
# 15.3 — query_stakeholder_positions routes by polarity
# ---------------------------------------------------------------------------


def test_negative_polarity_returns_legacy_stance() -> None:
    """Default behaviour (event_polarity='negative') must return the legacy
    `stakeholderDefaultStance` + `stakeholderPrecedent` — preserves all
    pre-Phase-15 callers."""
    from engine.ontology.intelligence import query_stakeholder_positions

    positions = query_stakeholder_positions(["regulatory_policy"], event_polarity="negative")
    assert len(positions) >= 1
    # Vedanta should appear in negative precedents (it's a real negative case)
    has_vedanta_or_neg = any(
        any(kw in p.precedent for kw in ["Vedanta", "YES Bank", "Wells Fargo", "Adani Group", "Maruti Manesar"])
        for p in positions
    )
    assert has_vedanta_or_neg, (
        f"Expected at least one negative-precedent in default flavour; got: "
        f"{[p.precedent[:60] for p in positions]}"
    )


def test_positive_polarity_omits_negative_precedents() -> None:
    """When event_polarity='positive', returned precedents must NOT include
    Vedanta / Wells Fargo / YES Bank moratorium — those are negative cases."""
    from engine.ontology.intelligence import query_stakeholder_positions

    positions = query_stakeholder_positions(
        ["climate_disclosure", "transition_announcement", "esg_rating_change", "sustainable_bonds"],
        event_polarity="positive",
    )
    assert len(positions) >= 1, "Positive polarity returned zero stakeholders — broken trigger keywords?"
    for p in positions:
        prec = p.precedent
        # No negative-event precedents leaking through
        for forbidden in ["Vedanta 2020 SCN", "Vedanta Konkola", "Wells Fargo 2016 BBB→B over fraud",
                          "YES Bank 2020 moratorium", "Maruti Manesar 2012", "Adani Mundra 2020 NGT"]:
            assert forbidden not in prec, (
                f"Stakeholder {p.label} positive precedent contains negative case "
                f"{forbidden!r}: {prec[:200]}"
            )


def test_positive_polarity_returns_upside_examples() -> None:
    """The positive-polarity precedents must mention real upside cases
    (Tata Power, Infosys, HDFC Bank, JSW Vijayanagar, Khavda, etc)."""
    from engine.ontology.intelligence import query_stakeholder_positions

    positions = query_stakeholder_positions(
        ["climate_disclosure", "transition_announcement", "esg_rating_change", "sustainable_bonds", "stewardship"],
        event_polarity="positive",
    )
    assert len(positions) >= 3, "Need enough stakeholders to cover upside flavour"
    joined = " ".join(p.precedent for p in positions)
    upside_keywords = ["Tata Power", "Infosys", "HDFC Bank", "Vijayanagar", "Khavda", "RE100", "DJSI", "A→AA"]
    matched = [kw for kw in upside_keywords if kw in joined]
    assert len(matched) >= 3, (
        f"Expected ≥ 3 upside reference cases in positive precedents; matched: {matched}"
    )


def test_back_compat_default_polarity_is_negative() -> None:
    """Old callers without event_polarity kwarg must keep the negative
    flavour (back-compat for existing pipelines)."""
    import inspect
    from engine.ontology.intelligence import query_stakeholder_positions

    sig = inspect.signature(query_stakeholder_positions)
    assert "event_polarity" in sig.parameters
    assert sig.parameters["event_polarity"].default == "negative"


# ---------------------------------------------------------------------------
# CEO dispatcher uses polarity
# ---------------------------------------------------------------------------


def test_ceo_user_prompt_routes_positive_polarity_for_contract_win() -> None:
    """The CEO user-prompt builder must call query_stakeholder_positions
    with event_polarity='positive' when the event is a contract win."""
    from engine.analysis.ceo_narrative_generator import _build_user_prompt
    from engine.analysis.insight_generator import DeepInsight
    from unittest.mock import MagicMock

    insight = DeepInsight(
        headline="Test contract win",
        impact_score=6,
        core_mechanism="x", profitability_connection="x", translation="x",
        impact_analysis={}, financial_timeline={}, esg_relevance_score={},
        net_impact_summary="x",
        decision_summary={"materiality": "MODERATE", "key_risk": "x", "top_opportunity": "x"},
        causal_chain={}, warnings=[],
    )
    result = MagicMock()
    result.title = "Waaree wins solar auction"
    result.event = MagicMock(event_id="event_contract_win")
    result.themes = MagicMock(primary_theme="Energy")

    company = MagicMock()
    company.name = "Waaree Energies"
    company.industry = "Renewable Energy"
    company.market_cap = "Mid Cap"
    company.primitive_calibration = {"revenue_cr": 14376, "fy_year": 2026, "debt_to_equity": 0.5}

    prompt = _build_user_prompt(insight, result, company)
    # Must explicitly tell the LLM the stakeholder context is positive-flavour
    assert "POSITIVE-EVENT FLAVOUR" in prompt, (
        "CEO prompt missing polarity label on stakeholder context — "
        "Phase 15.3 dispatcher not wired"
    )


def test_ceo_user_prompt_routes_negative_polarity_for_supply_chain_disruption() -> None:
    """Negative event must keep the existing stakeholder context flavour."""
    from engine.analysis.ceo_narrative_generator import _build_user_prompt
    from engine.analysis.insight_generator import DeepInsight
    from unittest.mock import MagicMock

    insight = DeepInsight(
        headline="LNG supply disrupted",
        impact_score=8,
        core_mechanism="x", profitability_connection="x", translation="x",
        impact_analysis={}, financial_timeline={}, esg_relevance_score={},
        net_impact_summary="x",
        decision_summary={"materiality": "HIGH", "key_risk": "regulatory", "top_opportunity": ""},
        causal_chain={}, warnings=[],
    )
    result = MagicMock()
    result.title = "JSW Energy LNG supply shock"
    result.event = MagicMock(event_id="event_supply_chain_disruption")
    result.themes = MagicMock(primary_theme="Energy")

    company = MagicMock()
    company.name = "JSW Energy"
    company.industry = "Power/Energy"
    company.market_cap = "Large Cap"
    company.primitive_calibration = {"revenue_cr": 11484, "fy_year": 2026, "debt_to_equity": 1.0}

    prompt = _build_user_prompt(insight, result, company)
    assert "NEGATIVE-EVENT FLAVOUR" in prompt
    assert "POSITIVE-EVENT FLAVOUR" not in prompt
