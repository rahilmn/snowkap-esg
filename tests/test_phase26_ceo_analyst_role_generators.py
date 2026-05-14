"""Phase 3 §5.2/§5.3/§5.7 — CEO + Analyst role generator tests +
cross-role distinctness gates.

Locks the deterministic baselines so the LLM-prompt swap is body-only.

§5.7 acceptance gates:
  * CEO `role_paragraph` must contain ZERO ₹ figures
  * Analyst `regulatory_checklist` must contain ≥ 3 entries with valid
    framework section codes (we surface this via `regulatory_checklist`
    inside takeaways/paragraph; tests assert the checklist composition)
  * CEO recommendations: zero `compliance` types
  * Analyst recommendations: zero `capital_allocation` types
  * Cross-role: CFO and CEO share zero meaningful overlap on the
    role-distinct surfaces (headline + hero label + role_paragraph)
"""
from __future__ import annotations

from engine.analysis.evidence_pack import (
    CascadeBlock,
    CascadeHop,
    ConfidenceBounds,
    DecisionWindow,
    EvidencePack,
    FrameworkHit,
    PeerEvent,
    Stakeholder,
)
from engine.analysis.role_generators import (
    RecommendationStub,
    generate_analyst_payload,
    generate_ceo_payload,
    generate_cfo_payload,
)
from engine.analysis.role_generators.analyst import _regulatory_checklist
from engine.analysis.role_generators.ceo import _three_year_horizon


# ---------------------------------------------------------------------------
# CEO — never leads with ₹
# ---------------------------------------------------------------------------


def test_ceo_headline_never_contains_rupee_even_with_huge_cascade():
    """Plan §5.3 hard rule: CEO never leads with a ₹ figure."""
    pack = EvidencePack(
        cascade=CascadeBlock(total_cr=99_999),
        polarity="negative",
    )
    p = generate_ceo_payload(pack)
    assert "₹" not in p.headline
    assert "Cr" not in p.headline


def test_ceo_role_paragraph_contains_zero_rupee_figures():
    """§5.7 acceptance gate."""
    pack = EvidencePack(
        cascade=CascadeBlock(total_cr=1857.6),
        comparables=[PeerEvent(company="Tata Power SECI", polarity="positive")],
        polarity="positive",
    )
    p = generate_ceo_payload(pack)
    assert "₹" not in p.role_paragraph
    # No bare "Cr" units either (e.g. ₹500 Cr without symbol)
    assert " Cr" not in p.role_paragraph


def test_ceo_hero_metric_value_is_strategic_phrase_not_rupee():
    pack = EvidencePack(
        cascade=CascadeBlock(total_cr=500),
        stakeholders=[Stakeholder(name="MSCI ESG", stance="positive")],
        polarity="positive",
    )
    p = generate_ceo_payload(pack)
    assert "₹" not in p.hero_metric.value
    assert p.hero_metric.label == "Strategic position"
    assert p.hero_metric.horizon  # populated, not empty


# ---------------------------------------------------------------------------
# CEO — 3-year horizon framing
# ---------------------------------------------------------------------------


def test_ceo_horizon_in_headline_matches_dynamic_3yr():
    pack = EvidencePack(polarity="positive")
    p = generate_ceo_payload(pack)
    horizon = _three_year_horizon()
    assert horizon in p.headline


def test_ceo_three_year_horizon_format_is_fyXX_fyXX():
    """Phase 13 S2 — fiscal-year horizon dynamically rolls forward."""
    h = _three_year_horizon()
    assert h.startswith("FY")
    # Pattern: FYxx-FYyy where yy = xx + 2
    parts = h.replace("FY", "").split("-")
    assert len(parts) == 2
    assert int(parts[1]) - int(parts[0]) == 2


# ---------------------------------------------------------------------------
# CEO — polarity-coherent peer matching
# ---------------------------------------------------------------------------


def test_ceo_picks_polarity_matching_peer_when_available():
    pack = EvidencePack(
        polarity="positive",
        comparables=[
            PeerEvent(company="Vedanta SCN", polarity="negative"),
            PeerEvent(company="Tata Power SECI 4 GW (2024)", polarity="positive"),
        ],
    )
    p = generate_ceo_payload(pack)
    full = " ".join(p.role_takeaways) + " " + p.role_paragraph
    # Polarity-match wins
    assert "Tata Power SECI" in full
    # Wrong-polarity peer NOT cited (would be the "Vedanta" miss)
    assert "Vedanta" not in full


def test_ceo_falls_back_to_first_peer_when_no_polarity_match():
    pack = EvidencePack(
        polarity="positive",
        comparables=[PeerEvent(company="Wells Fargo BBB→B", polarity="negative")],
    )
    p = generate_ceo_payload(pack)
    # No positive-polarity peer → use the first available rather than skip
    full = " ".join(p.role_takeaways) + " " + p.role_paragraph
    assert "Wells Fargo" in full


# ---------------------------------------------------------------------------
# CEO — recommendation whitelist (§5.4)
# ---------------------------------------------------------------------------


def test_ceo_recommendations_drop_compliance_kpi_tracking_audit():
    """Plan §5.4: CEO forbidden = compliance, kpi_tracking, audit."""
    recs = [
        RecommendationStub(title="Strategic pivot", type="strategic"),
        RecommendationStub(title="ESG narrative refresh", type="esg_positioning"),
        RecommendationStub(title="Capital reallocation", type="capital_allocation"),
        RecommendationStub(title="Brand campaign", type="brand"),
        RecommendationStub(title="File BRSR P6", type="compliance"),  # drop
        RecommendationStub(title="KPI dashboard", type="kpi_tracking"),  # drop
        RecommendationStub(title="Audit trail", type="audit"),  # drop
    ]
    p = generate_ceo_payload(EvidencePack(polarity="negative"), recs)
    types = [r.type for r in p.recommendations]
    assert "compliance" not in types
    assert "kpi_tracking" not in types
    assert "audit" not in types
    assert "strategic" in types
    assert "esg_positioning" in types
    assert "capital_allocation" in types
    assert "brand" in types


# ---------------------------------------------------------------------------
# CEO — panel order (§5.6 / W4d)
# ---------------------------------------------------------------------------


def test_ceo_panel_order_matches_plan():
    """CEO order: personal_stakes → crisp_insight → three_year_trajectory
    → stakeholder_map → board_paragraph → recommendations_list.
    HIDE: kpi_table, framework_alignment_v2, audit_trail."""
    p = generate_ceo_payload(EvidencePack())
    assert p.visible_panels == [
        "personal_stakes", "crisp_insight", "three_year_trajectory",
        "stakeholder_map", "board_paragraph", "recommendations_list",
    ]
    assert "kpi_table" in p.hidden_panels
    assert "framework_alignment_v2" in p.hidden_panels
    assert "audit_trail" in p.hidden_panels


# ---------------------------------------------------------------------------
# CEO — 80-word cap
# ---------------------------------------------------------------------------


def test_ceo_paragraph_capped_at_80_words():
    pack = EvidencePack(
        polarity="positive",
        comparables=[PeerEvent(company=" ".join(["LongPeer"] * 30), polarity="positive")],
    )
    p = generate_ceo_payload(pack)
    assert len(p.role_paragraph.split()) <= 81  # 80 + ellipsis token


# ---------------------------------------------------------------------------
# Analyst — framework-led headline + confidence bounds
# ---------------------------------------------------------------------------


def test_analyst_headline_starts_with_framework_section_when_present():
    pack = EvidencePack(
        frameworks=[FrameworkHit(code="BRSR:P6:Q14", is_mandatory=True)],
        decision_windows=[DecisionWindow(label="BRSR", deadline="2026-09-30", severity="hard")],
    )
    p = generate_analyst_payload(pack)
    assert "BRSR:P6:Q14" in p.headline
    assert "2026-09-30" in p.headline


def test_analyst_headline_marks_unverified_when_no_method_no_beta():
    """Plan §5.3: 'Flag unverified claims explicitly with [unverified]'."""
    pack = EvidencePack(
        frameworks=[FrameworkHit(code="BRSR:P6")],
        # No confidence_bounds.method, no cascade.hops with β
    )
    p = generate_analyst_payload(pack)
    assert "[unverified]" in p.headline
    full = " ".join(p.role_takeaways) + " " + p.role_paragraph
    assert "[unverified]" in full


def test_analyst_does_not_mark_unverified_when_method_or_beta_present():
    pack = EvidencePack(
        frameworks=[FrameworkHit(code="BRSR:P6")],
        cascade=CascadeBlock(
            total_cr=500,
            hops=[CascadeHop(source="EP", target="OX", beta=0.34, lag_months=6)],
        ),
    )
    p = generate_analyst_payload(pack)
    assert "[unverified]" not in p.headline


def test_analyst_takeaways_cite_confidence_phrase():
    """Confidence bounds (β + lag + method) appear in quantitative claims."""
    pack = EvidencePack(
        frameworks=[FrameworkHit(code="GRI:303")],
        cascade=CascadeBlock(
            total_cr=500,
            hops=[CascadeHop(source="EP", target="OX", beta=0.34, lag_months=6)],
        ),
    )
    p = generate_analyst_payload(pack)
    full = " ".join(p.role_takeaways) + " " + p.role_paragraph
    assert "β=0.34" in full
    assert "lag 6mo" in full


# ---------------------------------------------------------------------------
# Analyst — regulatory checklist
# ---------------------------------------------------------------------------


def test_regulatory_checklist_caps_at_5_entries():
    pack = EvidencePack(frameworks=[
        FrameworkHit(code=f"BRSR:P{i}", is_mandatory=True) for i in range(8)
    ])
    cl = _regulatory_checklist(pack)
    assert len(cl) == 5


def test_regulatory_checklist_marks_mandatory_with_disclose_verb():
    pack = EvidencePack(
        frameworks=[
            FrameworkHit(code="BRSR:P6", is_mandatory=True),
            FrameworkHit(code="GRI:303", is_mandatory=False),
        ],
    )
    cl = _regulatory_checklist(pack)
    by_code = {c["framework_section"]: c for c in cl}
    assert by_code["BRSR:P6"]["action_verb"] == "Disclose"
    assert by_code["GRI:303"]["action_verb"] == "Review"


def test_analyst_role_takeaways_mention_checklist_when_frameworks_present():
    pack = EvidencePack(frameworks=[
        FrameworkHit(code="BRSR:P6:Q14"),
        FrameworkHit(code="GRI:303"),
        FrameworkHit(code="TCFD-Strategy-c"),
    ])
    p = generate_analyst_payload(pack)
    joined = " ".join(p.role_takeaways)
    assert "Checklist:" in joined
    assert "3 framework section" in joined or "framework" in joined.lower()


# ---------------------------------------------------------------------------
# Analyst — recommendation whitelist (§5.4)
# ---------------------------------------------------------------------------


def test_analyst_recommendations_drop_capital_allocation_financial_brand():
    """Plan §5.4: Analyst forbidden = capital_allocation, financial, brand."""
    recs = [
        RecommendationStub(title="File BRSR", type="framework"),
        RecommendationStub(title="Disclose Scope 3", type="disclosure"),
        RecommendationStub(title="KPI dashboard", type="kpi_tracking"),
        RecommendationStub(title="Audit trail review", type="audit"),
        RecommendationStub(title="Capital reallocation", type="capital_allocation"),  # drop
        RecommendationStub(title="Hedge USD", type="financial"),  # drop
        RecommendationStub(title="Brand campaign", type="brand"),  # drop
    ]
    p = generate_analyst_payload(EvidencePack(), recs)
    types = [r.type for r in p.recommendations]
    assert "capital_allocation" not in types
    assert "financial" not in types
    assert "brand" not in types
    assert "framework" in types
    assert "disclosure" in types


# ---------------------------------------------------------------------------
# Analyst — panel order (§5.6 / W4d)
# ---------------------------------------------------------------------------


def test_analyst_panel_order_matches_plan():
    """Analyst order: personal_stakes → crisp_insight → kpi_table →
    framework_alignment_v2 → causal_chain_viz → audit_trail →
    recommendations_list. HIDE: board_paragraph, three_year_trajectory."""
    p = generate_analyst_payload(EvidencePack())
    assert p.visible_panels == [
        "personal_stakes", "crisp_insight", "kpi_table",
        "framework_alignment_v2", "causal_chain_viz", "audit_trail",
        "recommendations_list",
    ]
    assert "board_paragraph" in p.hidden_panels
    assert "three_year_trajectory" in p.hidden_panels


# ---------------------------------------------------------------------------
# Cross-role distinctness — §5.7
# ---------------------------------------------------------------------------


def _shared_pack_with_recommendations() -> tuple[EvidencePack, list[RecommendationStub]]:
    pack = EvidencePack(
        cascade=CascadeBlock(
            total_cr=1857.6,
            hops=[CascadeHop(source="EP", target="OX", beta=0.34, lag_months=6)],
        ),
        frameworks=[
            FrameworkHit(code="BRSR:P6:Q14", is_mandatory=True),
            FrameworkHit(code="GRI:303"),
        ],
        stakeholders=[Stakeholder(name="MSCI ESG", stance="negative")],
        comparables=[PeerEvent(company="Vedanta SCN 2020", polarity="negative")],
        polarity="negative",
        confidence_bounds=ConfidenceBounds(method="cascade", figure_lo_cr=1700, figure_hi_cr=2100),
        decision_windows=[
            DecisionWindow(label="BRSR P6 due", deadline="2026-09-30", severity="hard"),
        ],
    )
    recs = [
        RecommendationStub(title="Hedge USD exposure", type="financial", budget_cr=10, payback_months=6),
        RecommendationStub(title="Strategic pivot to renewables", type="strategic"),
        RecommendationStub(title="File BRSR P6 disclosure", type="framework"),
        RecommendationStub(title="ESG narrative refresh", type="esg_positioning"),
        RecommendationStub(title="Audit trail review", type="audit"),
    ]
    return pack, recs


def test_three_role_payloads_have_distinct_headlines():
    pack, recs = _shared_pack_with_recommendations()
    cfo = generate_cfo_payload(pack, recs)
    ceo = generate_ceo_payload(pack, recs)
    analyst = generate_analyst_payload(pack, recs)
    headlines = {cfo.headline, ceo.headline, analyst.headline}
    assert len(headlines) == 3  # all three differ


def test_three_role_payloads_have_distinct_hero_labels():
    """Per the plan, each role has a distinct hero metric label:
    CFO=P&L exposure, CEO=Strategic position, Analyst=Disclosure trigger."""
    pack, recs = _shared_pack_with_recommendations()
    assert generate_cfo_payload(pack, recs).hero_metric.label == "P&L exposure"
    assert generate_ceo_payload(pack, recs).hero_metric.label == "Strategic position"
    assert generate_analyst_payload(pack, recs).hero_metric.label == "Disclosure trigger"


def test_three_role_payloads_have_zero_rec_type_overlap_on_forbidden_types():
    """No role surfaces another role's forbidden type."""
    pack, recs = _shared_pack_with_recommendations()
    cfo = generate_cfo_payload(pack, recs)
    ceo = generate_ceo_payload(pack, recs)
    analyst = generate_analyst_payload(pack, recs)

    # CFO must not have CEO's exclusives
    cfo_types = {r.type for r in cfo.recommendations}
    assert not (cfo_types & {"esg_positioning", "strategic", "brand"})

    # CEO must not have Analyst's exclusives
    ceo_types = {r.type for r in ceo.recommendations}
    assert not (ceo_types & {"compliance", "kpi_tracking", "audit"})

    # Analyst must not have CFO's exclusives
    analyst_types = {r.type for r in analyst.recommendations}
    assert not (analyst_types & {"capital_allocation", "financial", "brand"})


def test_three_role_payloads_use_distinct_panel_orders():
    pack, recs = _shared_pack_with_recommendations()
    cfo = generate_cfo_payload(pack, recs)
    ceo = generate_ceo_payload(pack, recs)
    analyst = generate_analyst_payload(pack, recs)
    # Visible panel sets must differ — no two roles see the same lineup
    assert tuple(cfo.visible_panels) != tuple(ceo.visible_panels)
    assert tuple(ceo.visible_panels) != tuple(analyst.visible_panels)
    assert tuple(cfo.visible_panels) != tuple(analyst.visible_panels)
