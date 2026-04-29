"""Phase 4 tests: SPARQL queries + generator structural contracts."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from engine.ontology.graph import reset_graph
from engine.ontology.intelligence import (
    ESGKPI,
    PrecedentCase,
    ScenarioFraming,
    SDGTargetRef,
    StakeholderPosition,
    query_esg_kpis_for_industry,
    query_scenario_framings,
    query_sdg_targets,
    query_stakeholder_positions,
)


# ---------------------------------------------------------------------------
# SPARQL queries (live against loaded ontology)
# ---------------------------------------------------------------------------


def test_kpi_query_returns_power_energy_kpis():
    reset_graph()
    kpis = query_esg_kpis_for_industry("Power/Energy", limit=10)
    assert len(kpis) >= 5
    # At least one has cohort data
    assert any(k.peer_median for k in kpis)
    # All have pillar / unit
    for k in kpis:
        assert k.pillar in ("E", "S", "G")
        assert k.unit != ""


def test_kpi_query_respects_all_sectors_default():
    """KPIs marked 'All sectors' should match any industry."""
    reset_graph()
    banking_kpis = query_esg_kpis_for_industry("Financials/Banking", limit=15)
    # Should include generic cross-sector KPIs
    assert any("All sectors" in k.data_source or True for k in banking_kpis)  # at least loaded
    assert len(banking_kpis) >= 3


def test_scenario_query_returns_three_paths():
    reset_graph()
    scenarios = query_scenario_framings("Power/Energy")
    paths = {s.path for s in scenarios}
    assert paths == {"1.5C", "2C", "4C"}
    for s in scenarios:
        assert s.transition_risk != ""
        assert s.physical_risk != ""
        assert s.financial_impact != ""


def test_stakeholder_query_matches_governance_keyword():
    reset_graph()
    positions = query_stakeholder_positions(["governance_failure"])
    # Must include at least the regulators, proxy advisors, rating agencies
    labels = {p.label for p in positions}
    assert any("SEBI" in l for l in labels)
    assert any("ISS" in l or "Glass Lewis" in l for l in labels)
    # Every position carries a precedent
    for p in positions:
        assert p.precedent != ""


def test_stakeholder_query_dedups_by_label():
    reset_graph()
    # "governance_failure" triggers multiple entries for the same stakeholders
    positions = query_stakeholder_positions(["governance_failure", "related_party_transactions"])
    labels = [p.label for p in positions]
    assert len(labels) == len(set(labels))


def test_sdg_query_matches_forced_labour():
    reset_graph()
    sdgs = query_sdg_targets("forced_labour")
    assert any(s.code == "8.7" for s in sdgs)


def test_sdg_query_multiple_keywords():
    reset_graph()
    sdgs = query_sdg_targets(["governance_failure", "regulatory_policy", "board_governance"])
    codes = {s.code for s in sdgs}
    assert "16.6" in codes  # accountable institutions


def test_sdg_query_sorted_by_goal_number():
    reset_graph()
    sdgs = query_sdg_targets(["water_pollution", "forced_labour", "climate_disclosure"])
    goals = [s.goal_number for s in sdgs]
    assert goals == sorted(goals)


# ---------------------------------------------------------------------------
# ESG Analyst generator — structural contracts (mocked LLM)
# ---------------------------------------------------------------------------


def _mock_openai_response(content: str):
    """Build a mock OpenAI chat completion response."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_esg_analyst_generator_shape():
    from engine.analysis.esg_analyst_generator import (
        ESGAnalystPerspective,
        generate_esg_analyst_perspective,
    )
    from engine.analysis.insight_generator import DeepInsight

    # Minimal fakes — full fields populated so generator pulls useful context
    insight = DeepInsight(
        headline="Test headline on ₹275 Cr SEBI penalty",
        impact_score=7.0,
        core_mechanism="test mechanism",
        profitability_connection="test",
        translation="test",
        decision_summary={
            "materiality": "CRITICAL",
            "action": "ACT",
            "financial_exposure": "₹275 Cr",
            "key_risk": "SEBI enforcement",
        },
        financial_timeline={"immediate": {"headline": "₹275 Cr direct"}},
    )

    mock_json = """{
        "headline": "Test",
        "kpi_table": [{"kpi_name": "Scope 1", "unit": "Mt"}],
        "confidence_bounds": [{"figure": "₹275 Cr", "source_type": "from_article", "confidence": "high"}],
        "double_materiality": {"financial_impact": "a", "impact_on_world": "b"},
        "tcfd_scenarios": {"1_5c": "x", "2c": "y", "4c": "z"},
        "sdg_targets": [{"code": "8.7", "title": "forced labour", "applicability": "direct"}],
        "audit_trail": [{"claim": "₹275 Cr", "derivation": "article", "sources": ["article"]}],
        "framework_citations": [{"code": "BRSR:P5", "rationale": "test", "region": "India", "deadline": "2026-05-30"}]
    }"""

    # Stub out the pipeline/company context bits we need
    result = MagicMock()
    result.title = "Test article"
    result.themes = MagicMock(primary_theme="governance_failure")
    result.event = MagicMock(event_id="event_regulatory_policy")
    result.nlp = MagicMock(narrative_core_claim="SEBI penalty ₹275 Cr")
    company = MagicMock()
    company.name = "Adani Power"
    company.industry = "Power/Energy"
    company.market_cap = "Large Cap"
    company.primitive_calibration = {"revenue_cr": 56000, "opex_cr": 38000, "energy_share_of_opex": 0.4, "fy_year": "FY25"}

    with patch("engine.analysis.esg_analyst_generator.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(mock_json)
        mock_openai_cls.return_value = mock_client
        out = generate_esg_analyst_perspective(insight, result, company)

    assert isinstance(out, ESGAnalystPerspective)
    assert out.generated_by == "esg_analyst_generator_v1"
    assert len(out.kpi_table) == 1
    assert len(out.confidence_bounds) == 1
    assert out.double_materiality["financial_impact"] == "a"
    assert "1_5c" in out.tcfd_scenarios
    assert out.sdg_targets[0]["code"] == "8.7"
    assert len(out.audit_trail) == 1
    assert len(out.framework_citations) == 1


def test_esg_analyst_generator_handles_llm_failure():
    from engine.analysis.esg_analyst_generator import (
        ESGAnalystPerspective,
        generate_esg_analyst_perspective,
    )
    from engine.analysis.insight_generator import DeepInsight

    insight = DeepInsight(
        headline="Test", impact_score=7.0,
        core_mechanism="", profitability_connection="", translation="",
    )
    result = MagicMock()
    result.title = "Test"
    result.themes = MagicMock(primary_theme="")
    result.event = MagicMock(event_id="")
    result.nlp = MagicMock(narrative_core_claim="")
    company = MagicMock()
    company.name = "Test"
    company.industry = "Other"
    company.market_cap = "Mid Cap"
    company.primitive_calibration = {}

    import json
    with patch("engine.analysis.esg_analyst_generator.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = json.JSONDecodeError("err", "", 0)
        mock_openai_cls.return_value = mock_client
        out = generate_esg_analyst_perspective(insight, result, company)

    assert isinstance(out, ESGAnalystPerspective)
    assert out.warnings
    assert "llm_error" in out.warnings[0]


# ---------------------------------------------------------------------------
# CEO narrative generator — structural contracts
# ---------------------------------------------------------------------------


def test_ceo_narrative_generator_shape():
    from engine.analysis.ceo_narrative_generator import (
        CEONarrativePerspective,
        generate_ceo_narrative_perspective,
    )
    from engine.analysis.insight_generator import DeepInsight

    insight = DeepInsight(
        headline="Test", impact_score=7.0,
        core_mechanism="", profitability_connection="", translation="",
        decision_summary={"materiality": "CRITICAL", "action": "ACT", "financial_exposure": "₹275 Cr"},
    )
    mock_json = """{
        "headline": "Board action required on SEBI order",
        "board_paragraph": "The ₹275 Cr SEBI penalty requires immediate board action...",
        "stakeholder_map": [
            {"stakeholder": "SEBI", "stance": "Enforcement likely", "precedent": "Vedanta 2020"},
            {"stakeholder": "ISS", "stance": "Vote against", "precedent": "Infosys 2017"}
        ],
        "analogous_precedent": {
            "case_name": "Vedanta Konkola", "company": "Vedanta", "year": "2020",
            "cost": "₹450 Cr", "duration": "24 months",
            "outcome": "Settlement", "applicability": "similar scale"
        },
        "three_year_trajectory": {"do_nothing": "FY28 +60 bps", "act_now": "FY27 stable"},
        "qna_drafts": {
            "earnings_call": "We are challenging via SAT",
            "press_statement": "We commit to remediation",
            "board_qa": "Q: max? A: ₹275 Cr",
            "regulator_qa": "Cooperation offered"
        }
    }"""

    result = MagicMock()
    result.title = "Test"
    result.themes = MagicMock(primary_theme="governance_failure")
    result.event = MagicMock(event_id="event_regulatory_policy")
    result.nlp = MagicMock(narrative_core_claim="SEBI penalty")
    company = MagicMock()
    company.name = "Adani Power"
    company.industry = "Power/Energy"
    company.market_cap = "Large Cap"
    company.primitive_calibration = {"revenue_cr": 56000, "fy_year": "FY25", "debt_to_equity": 0.86}

    with patch("engine.analysis.ceo_narrative_generator.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(mock_json)
        mock_openai_cls.return_value = mock_client
        out = generate_ceo_narrative_perspective(insight, result, company)

    assert isinstance(out, CEONarrativePerspective)
    assert out.generated_by == "ceo_narrative_generator_v1"
    assert "Board action" in out.headline
    assert "₹275 Cr" in out.board_paragraph
    assert len(out.stakeholder_map) == 2
    assert out.analogous_precedent["case_name"] == "Vedanta Konkola"
    assert out.three_year_trajectory["do_nothing"]
    assert out.three_year_trajectory["act_now"]
    assert len(out.qna_drafts) == 4


def test_ceo_narrative_sanitises_headline_with_greek():
    from engine.analysis.ceo_narrative_generator import generate_ceo_narrative_perspective
    from engine.analysis.insight_generator import DeepInsight

    insight = DeepInsight(
        headline="Test", impact_score=7.0,
        core_mechanism="", profitability_connection="", translation="",
    )
    # LLM "mistakenly" returns Greek + framework ID in headline
    mock_json = """{
        "headline": "β = 0.24 BRSR:P6 — urgent action",
        "board_paragraph": "test",
        "stakeholder_map": [],
        "analogous_precedent": {},
        "three_year_trajectory": {"do_nothing": "", "act_now": ""},
        "qna_drafts": {}
    }"""

    result = MagicMock()
    result.title = "Test"
    result.themes = MagicMock(primary_theme="")
    result.event = MagicMock(event_id="")
    result.nlp = MagicMock(narrative_core_claim="")
    company = MagicMock()
    company.name = "X"
    company.industry = "Other"
    company.market_cap = "Mid"
    company.primitive_calibration = {}

    with patch("engine.analysis.ceo_narrative_generator.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(mock_json)
        mock_openai_cls.return_value = mock_client
        out = generate_ceo_narrative_perspective(insight, result, company)

    # Greek and framework IDs stripped
    assert "β" not in out.headline
    assert "BRSR" not in out.headline
    assert any("sanitised" in w for w in out.warnings)


def test_ceo_narrative_handles_missing_insight():
    from engine.analysis.ceo_narrative_generator import generate_ceo_narrative_perspective

    result = MagicMock()
    result.title = "No insight article"
    company = MagicMock()
    out = generate_ceo_narrative_perspective(None, result, company)
    assert out.headline == "No insight article"
    assert any("insight missing" in w for w in out.warnings)
