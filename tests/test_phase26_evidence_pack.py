"""Phase 3 §5.1 — EvidencePack dataclass + builder tests.

Validates the structural contract: every field present, builder
tolerant of missing inputs, derived figures pulled correctly from
existing pipeline + insight outputs.

The EvidencePack is the target shape for the deferred Stage 10 split.
Today it's built and discarded; tests here lock the contract so the
follow-up role-generator workstream has a stable foundation.
"""
from __future__ import annotations

from types import SimpleNamespace

from engine.analysis.evidence_pack import (
    CascadeBlock,
    CausalChain,
    ConfidenceBounds,
    DecisionWindow,
    EvidencePack,
    FrameworkHit,
    PainpointMatch,
    PeerEvent,
    Stakeholder,
    build_evidence_pack,
)


# ---------------------------------------------------------------------------
# Dataclass shape locks
# ---------------------------------------------------------------------------


def test_evidence_pack_has_all_nine_plan_fields():
    """Plan §5.1 enumerates 9 fields. Renaming any of these breaks the
    contract the role generators will consume."""
    pack = EvidencePack()
    expected = {
        "cascade", "frameworks", "stakeholders", "painpoint_matches",
        "causal_chain", "comparables", "polarity", "confidence_bounds",
        "decision_windows",
    }
    assert set(pack.__dataclass_fields__.keys()) == expected


def test_evidence_pack_to_dict_is_json_friendly():
    import json
    pack = EvidencePack(
        cascade=CascadeBlock(total_cr=500.0, margin_bps=12.5),
        frameworks=[FrameworkHit(code="BRSR:P6:Q14", name="BRSR")],
        stakeholders=[Stakeholder(name="SEBI", stance="negative")],
        painpoint_matches=[PainpointMatch(topic="climate", similarity=0.7)],
        causal_chain=CausalChain(hops=2, relationship_type="directOperational"),
        comparables=[PeerEvent(company="Tata Power", polarity="positive")],
        polarity="positive",
        confidence_bounds=ConfidenceBounds(figure_lo_cr=450, figure_hi_cr=550),
        decision_windows=[DecisionWindow(label="BRSR P6", deadline="2026-09-30")],
    )
    js = json.dumps(pack.to_dict())
    parsed = json.loads(js)
    assert parsed["polarity"] == "positive"
    assert parsed["cascade"]["total_cr"] == 500.0
    assert parsed["frameworks"][0]["code"] == "BRSR:P6:Q14"
    assert parsed["stakeholders"][0]["name"] == "SEBI"


# ---------------------------------------------------------------------------
# Builder — defensive on empty inputs
# ---------------------------------------------------------------------------


def test_build_evidence_pack_empty_inputs_returns_empty_pack():
    """A bare PipelineResult + empty insight must not crash."""
    stub = SimpleNamespace(frameworks=[], causal_chains=[])
    pack = build_evidence_pack(stub, {})
    assert pack.cascade.total_cr == 0.0
    assert pack.frameworks == []
    assert pack.stakeholders == []
    assert pack.causal_chain.hops == 0
    assert pack.comparables == []
    assert pack.polarity == "neutral"


def test_build_evidence_pack_handles_none_insight():
    stub = SimpleNamespace(frameworks=[], causal_chains=[])
    pack = build_evidence_pack(stub)
    assert isinstance(pack, EvidencePack)


# ---------------------------------------------------------------------------
# Builder — derives cascade total from decision_summary
# ---------------------------------------------------------------------------


def test_cascade_total_extracts_largest_rupee_figure():
    stub = SimpleNamespace(frameworks=[], causal_chains=[])
    insight = {
        "decision_summary": {
            "financial_exposure": "₹500 Cr (engine estimate)",
            "key_risk": "of which ₹100 Cr Q1",
            "top_opportunity": "₹50 Cr upside",
        },
        "net_impact_summary": "Total ~₹500 Cr exposure over Q4.",
    }
    pack = build_evidence_pack(stub, insight)
    assert pack.cascade.total_cr == 500.0


def test_cascade_total_zero_when_no_rupee_figures():
    stub = SimpleNamespace(frameworks=[], causal_chains=[])
    insight = {
        "decision_summary": {
            "financial_exposure": "Stakeholder reaction expected",
            "key_risk": "Reputational",
        },
    }
    pack = build_evidence_pack(stub, insight)
    assert pack.cascade.total_cr == 0.0


# ---------------------------------------------------------------------------
# Builder — frameworks
# ---------------------------------------------------------------------------


def test_build_frameworks_handles_dataclass_with_triggered_sections():
    """FrameworkMatch dataclass with multiple section codes → one
    FrameworkHit per section so the analyst gets each citation."""
    fm = SimpleNamespace(
        framework_id="BRSR", name="BRSR India",
        triggered_sections=["P6:Q14", "P9:Q1"],
        rationale="Climate risk", region="INDIA",
        is_mandatory=True,
    )
    fm.to_dict = lambda: {
        "framework_id": "BRSR", "name": "BRSR India",
        "triggered_sections": ["P6:Q14", "P9:Q1"],
        "rationale": "Climate risk", "region": "INDIA",
        "is_mandatory": True,
    }
    stub = SimpleNamespace(frameworks=[fm], causal_chains=[])
    pack = build_evidence_pack(stub, {})
    assert len(pack.frameworks) == 2
    assert pack.frameworks[0].code == "BRSR:P6:Q14"
    assert pack.frameworks[1].code == "BRSR:P9:Q1"
    assert pack.frameworks[0].is_mandatory is True
    assert pack.frameworks[0].region == "INDIA"


def test_build_frameworks_falls_back_to_framework_id_when_no_sections():
    fm = SimpleNamespace(
        framework_id="TCFD", name="TCFD",
        triggered_sections=[], rationale="", region="",
        is_mandatory=False,
    )
    fm.to_dict = lambda: {
        "framework_id": "TCFD", "name": "TCFD",
        "triggered_sections": [], "rationale": "",
        "region": "", "is_mandatory": False,
    }
    stub = SimpleNamespace(frameworks=[fm], causal_chains=[])
    pack = build_evidence_pack(stub, {})
    assert len(pack.frameworks) == 1
    assert pack.frameworks[0].code == "TCFD"


# ---------------------------------------------------------------------------
# Builder — causal chain (picks highest impact)
# ---------------------------------------------------------------------------


def test_causal_chain_picks_highest_impact():
    chains = [
        SimpleNamespace(hops=2, relationship_type="r1", explanation="weak", impact_score=2.0),
        SimpleNamespace(hops=3, relationship_type="r2", explanation="strong", impact_score=8.5),
        SimpleNamespace(hops=1, relationship_type="r3", explanation="medium", impact_score=4.0),
    ]
    stub = SimpleNamespace(frameworks=[], causal_chains=chains)
    pack = build_evidence_pack(stub, {})
    assert pack.causal_chain.impact_score == 8.5
    assert pack.causal_chain.hops == 3
    assert pack.causal_chain.relationship_type == "r2"


# ---------------------------------------------------------------------------
# Builder — stakeholders pulled from CEO perspective stakeholder_map
# ---------------------------------------------------------------------------


def test_build_stakeholders_from_ceo_perspective():
    stub = SimpleNamespace(frameworks=[], causal_chains=[])
    insight = {
        "perspectives": {
            "ceo": {
                "stakeholder_map": [
                    {"stakeholder": "SEBI", "stance": "negative", "precedent": "Vedanta SCN"},
                    {"name": "MSCI", "stance": "negative", "precedent": "Tata Steel downgrade"},
                ],
            },
        },
    }
    pack = build_evidence_pack(stub, insight)
    assert len(pack.stakeholders) == 2
    assert pack.stakeholders[0].name == "SEBI"
    assert pack.stakeholders[0].stance == "negative"
    assert "Vedanta SCN" in pack.stakeholders[0].precedent


# ---------------------------------------------------------------------------
# Builder — comparables from analogous_precedent
# ---------------------------------------------------------------------------


def test_comparables_from_analogous_precedent():
    stub = SimpleNamespace(frameworks=[], causal_chains=[])
    insight = {
        "decision_summary": {
            "analogous_precedent": "Tata Power SECI 4 GW (2024)",
            "materiality": "HIGH",
        },
        "event_polarity": "positive",
    }
    pack = build_evidence_pack(stub, insight)
    assert len(pack.comparables) == 1
    assert pack.comparables[0].company.startswith("Tata Power SECI")
    assert pack.comparables[0].polarity == "positive"


def test_comparables_skipped_when_precedent_is_null_string():
    """An LLM may emit the literal string 'null' when the ontology has
    no matching precedent — don't surface that as a comparable."""
    stub = SimpleNamespace(frameworks=[], causal_chains=[])
    insight = {
        "decision_summary": {"analogous_precedent": "null"},
    }
    pack = build_evidence_pack(stub, insight)
    assert pack.comparables == []


# ---------------------------------------------------------------------------
# Builder — painpoint matches from criticality block
# ---------------------------------------------------------------------------


def test_painpoint_matches_surfaced_when_aggregate_score_nonzero():
    stub = SimpleNamespace(frameworks=[], causal_chains=[])
    insight = {
        "criticality": {
            "score": 0.7,
            "components": {"painpoint_match": 0.85},
        },
    }
    pack = build_evidence_pack(stub, insight)
    assert len(pack.painpoint_matches) == 1
    assert pack.painpoint_matches[0].topic == "aggregate"
    assert pack.painpoint_matches[0].similarity == 0.85


def test_painpoint_matches_empty_when_score_zero():
    stub = SimpleNamespace(frameworks=[], causal_chains=[])
    insight = {"criticality": {"score": 0.5, "components": {"painpoint_match": 0}}}
    pack = build_evidence_pack(stub, insight)
    assert pack.painpoint_matches == []


# ---------------------------------------------------------------------------
# Builder — polarity inference
# ---------------------------------------------------------------------------


def test_polarity_uses_explicit_event_polarity():
    stub = SimpleNamespace(frameworks=[], causal_chains=[])
    pack = build_evidence_pack(stub, {"event_polarity": "positive"})
    assert pack.polarity == "positive"


def test_polarity_falls_back_to_negative_when_high_materiality():
    stub = SimpleNamespace(frameworks=[], causal_chains=[])
    pack = build_evidence_pack(stub, {
        "decision_summary": {"materiality": "CRITICAL"},
    })
    assert pack.polarity == "negative"


def test_polarity_filters_invalid_values():
    stub = SimpleNamespace(frameworks=[], causal_chains=[])
    pack = build_evidence_pack(stub, {"event_polarity": "yolo"})
    assert pack.polarity == "neutral"


# ---------------------------------------------------------------------------
# Builder — decision windows from financial_timeline
# ---------------------------------------------------------------------------


def test_decision_windows_from_financial_timeline():
    stub = SimpleNamespace(frameworks=[], causal_chains=[])
    insight = {
        "financial_timeline": {
            "next_earnings": "2026-07-22",
            "regulatory_deadline": "BRSR P6 due 2026-09-30",
        },
    }
    pack = build_evidence_pack(stub, insight)
    assert len(pack.decision_windows) == 2
    labels = [w.label for w in pack.decision_windows]
    assert "Next Earnings" in labels
    assert "Regulatory Deadline" in labels
