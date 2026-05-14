"""Phase 3 §5.2 — CFO role generator contract tests.

Locks the RoleDistinctPayload shape + CFO-specific behaviour so the
future LLM-prompt version (per §5.3) can swap the body without
breaking downstream consumers.

§5.7 acceptance gates this file enforces today (deterministic baseline):
  * CFO `role_takeaways[0]` first 60 chars must contain a ₹ figure
  * CFO recommendations: zero `esg_positioning` types
  * Headline leads with ₹ figure (sig2-rounded, en-IN grouping)
  * No strategic / 3-year / positioning language in the deterministic version
"""
from __future__ import annotations

from engine.analysis.evidence_pack import (
    CascadeBlock,
    DecisionWindow,
    EvidencePack,
    PeerEvent,
)
from engine.analysis.role_generators import (
    HeroMetric,
    RecommendationStub,
    RoleDistinctPayload,
    generate_cfo_payload,
)


# ---------------------------------------------------------------------------
# Contract — RoleDistinctPayload shape
# ---------------------------------------------------------------------------


def test_role_distinct_payload_has_plan_required_fields():
    """Plan §5.2 enumerates 7 fields. Renaming any breaks downstream."""
    p = RoleDistinctPayload(
        role="cfo",
        headline="x",
        hero_metric=HeroMetric(value="x", label="x"),
    )
    expected = {
        "role", "headline", "hero_metric", "role_takeaways",
        "role_paragraph", "recommendations", "visible_panels",
        "hidden_panels",
    }
    assert set(p.__dataclass_fields__.keys()) == expected


def test_payload_to_dict_is_json_friendly():
    import json
    p = generate_cfo_payload(
        EvidencePack(cascade=CascadeBlock(total_cr=500.0))
    )
    js = json.dumps(p.to_dict())
    parsed = json.loads(js)
    assert parsed["role"] == "cfo"
    assert "hero_metric" in parsed


# ---------------------------------------------------------------------------
# Acceptance §5.7 — CFO role_takeaways[0] first 60 chars contains ₹
# ---------------------------------------------------------------------------


def test_first_takeaway_contains_rupee_figure_in_first_60_chars():
    pack = EvidencePack(cascade=CascadeBlock(total_cr=1857.6))
    p = generate_cfo_payload(pack)
    assert p.role_takeaways  # non-empty
    head = p.role_takeaways[0][:60]
    assert "₹" in head


def test_headline_leads_with_rupee_when_cascade_present():
    pack = EvidencePack(cascade=CascadeBlock(total_cr=1857.6))
    p = generate_cfo_payload(pack)
    # ₹ must be in the headline; sig2 rounding → 1,900
    assert "₹" in p.headline
    assert "1,900" in p.headline


def test_headline_falls_back_when_cascade_zero():
    pack = EvidencePack(cascade=CascadeBlock(total_cr=0.0))
    p = generate_cfo_payload(pack)
    # Don't fabricate a ₹ figure when none exists
    assert "₹" not in p.headline
    assert "pending" in p.headline.lower() or "cascade" in p.headline.lower()


# ---------------------------------------------------------------------------
# Acceptance §5.7 — CFO recommendations: zero forbidden types
# ---------------------------------------------------------------------------


def test_cfo_recommendations_drop_esg_positioning_strategic_brand():
    """Plan §5.4 whitelist: CFO allowed = financial/operational/compliance.
    Forbidden types must be filtered out of the role payload."""
    recs = [
        RecommendationStub(title="Hedge ₹500 Cr", type="financial"),
        RecommendationStub(title="Refresh ESG narrative", type="esg_positioning"),
        RecommendationStub(title="3-year roadmap", type="strategic"),
        RecommendationStub(title="Brand campaign", type="brand"),
        RecommendationStub(title="File BRSR P6", type="compliance"),
    ]
    p = generate_cfo_payload(
        EvidencePack(cascade=CascadeBlock(total_cr=500)), recommendations=recs,
    )
    types = [r.type for r in p.recommendations]
    assert "esg_positioning" not in types
    assert "strategic" not in types
    assert "brand" not in types
    # Allowed types kept
    assert "financial" in types
    assert "compliance" in types


def test_cfo_recommendations_keep_untyped():
    """Untyped recs (legacy) flow through — only EXPLICITLY forbidden types
    are dropped."""
    recs = [RecommendationStub(title="Untyped action", type="")]
    p = generate_cfo_payload(
        EvidencePack(cascade=CascadeBlock(total_cr=500)), recommendations=recs,
    )
    assert len(p.recommendations) == 1


# ---------------------------------------------------------------------------
# Hero metric + decision window
# ---------------------------------------------------------------------------


def test_hero_metric_value_is_rounded_rupee():
    pack = EvidencePack(cascade=CascadeBlock(total_cr=1857.6))
    p = generate_cfo_payload(pack)
    assert "₹" in p.hero_metric.value
    assert p.hero_metric.label == "P&L exposure"


def test_hero_metric_decision_window_prefers_hard_deadline():
    pack = EvidencePack(
        cascade=CascadeBlock(total_cr=500),
        decision_windows=[
            DecisionWindow(label="Soft thing", deadline="2026-12-01", severity="soft"),
            DecisionWindow(label="BRSR P6 due", deadline="2026-09-30", severity="hard"),
        ],
    )
    p = generate_cfo_payload(pack)
    # Hard deadline wins
    assert p.hero_metric.decision_window == "2026-09-30"


def test_hero_metric_decision_window_falls_back_to_first_when_no_hard():
    pack = EvidencePack(
        cascade=CascadeBlock(total_cr=500),
        decision_windows=[
            DecisionWindow(label="Earnings", deadline="2026-07-22", severity="soft"),
        ],
    )
    p = generate_cfo_payload(pack)
    assert p.hero_metric.decision_window == "2026-07-22"


# ---------------------------------------------------------------------------
# Peer comparable + revenue ratio
# ---------------------------------------------------------------------------


def test_takeaway_includes_peer_when_present():
    pack = EvidencePack(
        cascade=CascadeBlock(total_cr=500),
        comparables=[PeerEvent(company="Tata Power SECI 4 GW (2024)")],
    )
    p = generate_cfo_payload(pack)
    full = " ".join(p.role_takeaways)
    assert "Tata Power" in full


def test_takeaway_includes_revenue_pct_when_company_revenue_provided():
    pack = EvidencePack(cascade=CascadeBlock(total_cr=500))
    p = generate_cfo_payload(pack, company_revenue_cr=10_000)
    # 500 / 10000 = 5.0%
    head = p.role_takeaways[0]
    assert "5.0%" in head or "% of revenue" in head


# ---------------------------------------------------------------------------
# Action recommendation framing
# ---------------------------------------------------------------------------


def test_takeaway_appends_action_with_payback_when_rec_has_payback():
    rec = RecommendationStub(
        title="Hedge USD exposure",
        type="financial",
        budget_cr=10.0,
        payback_months=6,
    )
    pack = EvidencePack(cascade=CascadeBlock(total_cr=500))
    p = generate_cfo_payload(pack, recommendations=[rec])
    joined = " ".join(p.role_takeaways)
    assert "Hedge USD exposure" in joined
    assert "payback 6mo" in joined
    assert "budget" in joined


# ---------------------------------------------------------------------------
# Defensive empty inputs
# ---------------------------------------------------------------------------


def test_generator_with_empty_pack_returns_safe_default():
    p = generate_cfo_payload(EvidencePack())
    assert p.role == "cfo"
    assert p.role_takeaways  # non-empty fallback
    assert p.role_paragraph
    assert p.hero_metric.label == "P&L exposure"


# ---------------------------------------------------------------------------
# Panel ordering (plan §5.6)
# ---------------------------------------------------------------------------


def test_cfo_panel_order_matches_plan_w4d():
    """Plan §5.6: CFO order = personal_stakes → crisp_insight → impact_metrics
    → recommendations_list → audit_trail. HIDE: narrative deep view, SDG, causal."""
    p = generate_cfo_payload(EvidencePack(cascade=CascadeBlock(total_cr=500)))
    assert p.visible_panels == [
        "personal_stakes", "crisp_insight", "impact_metrics",
        "recommendations_list", "audit_trail",
    ]
    assert "narrative_intelligence" in p.hidden_panels
    assert "sdg_map" in p.hidden_panels
    assert "causal_chain_viz" in p.hidden_panels


# ---------------------------------------------------------------------------
# Word cap (§5.3 — 90 words on role_paragraph)
# ---------------------------------------------------------------------------


def test_role_paragraph_capped_at_90_words():
    """Compose a pack that would naturally produce a long paragraph
    and assert the cap fires."""
    long_recs = [
        RecommendationStub(
            title=" ".join(["filler"] * 30),
            type="financial",
            budget_cr=100.0,
            payback_months=12,
        ),
    ]
    pack = EvidencePack(
        cascade=CascadeBlock(total_cr=500),
        comparables=[PeerEvent(company=" ".join(["LongPeer"] * 25))],
    )
    p = generate_cfo_payload(pack, recommendations=long_recs)
    word_count = len(p.role_paragraph.split())
    assert word_count <= 91  # 90 + the trailing ellipsis token if added
