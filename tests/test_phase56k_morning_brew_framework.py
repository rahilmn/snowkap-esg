"""Phase 56.K — the emailed Morning-Brew brief must also carry the framework
hit (BRSR principle + mandatory flag + interpretation), and the methodology
disclaimer must (a) state the ontology provenance of framework mappings and
(b) NOT promise the non-existent per-section "(i) icons"."""
from __future__ import annotations

import re

from engine.output.newsletter_morning_brew import render_article_morning_brew


def _payload(framework_hit=None, rec_hit=None):
    rec = {"title": "Model the FY27 CAFE-3 fleet-CO2 gap", "owner": "ESG / Finance"}
    if rec_hit is not None:
        rec["framework_hit"] = rec_hit
    wit = {"recommended_actions": [rec]}
    if framework_hit is not None:
        wit["framework_hit"] = framework_hit
    return {
        "article": {"title": "CAFE-3 norms tussle", "url": "http://x/cafe3",
                    "source": "CarToq", "company_slug": "maruti-suzuki-india", "id": "aid1"},
        "insight": {"analysis": {
            "what_changed": {"headline": "Maruti writes to PMO over CAFE-3", "source": "CarToq"},
            "why_it_matters": {"materiality_band": "MEDIUM", "criticality_summary": "Super-credits cut."},
            "what_it_triggers": wit,
            "what_to_watch": {"sentiment_trajectory": {}, "top_risk_categories": []},
        }},
    }


_BRSR_HIT = {
    "framework": "BRSR", "principle_code": "BRSR:P6",
    "principle_title": "Principle 6 — Environmental Protection", "mandatory": True,
    "interpretation": "Under BRSR Principle 6, the CAFE-3 draft is a material regulatory-transition risk.",
}


def test_email_renders_article_level_framework_hit():
    html = render_article_morning_brew(payload=_payload(framework_hit=_BRSR_HIT),
                                       company_name="Maruti Suzuki India")
    assert "How this hits your framework" in html
    assert "BRSR" in html and "BRSR:P6" in html
    assert "MANDATORY" in html                                   # India → mandatory chip
    assert "Principle 6" in html
    assert "material regulatory-transition risk" in html         # interpretation prose
    # placed between Recommended actions and Forward indicators
    assert html.index("Recommended actions") < html.index("How this hits your framework") < html.index("Forward indicators")


def test_email_falls_back_to_recommendation_hit():
    """No article-level hit → use the top recommendation's hit."""
    html = render_article_morning_brew(payload=_payload(rec_hit=_BRSR_HIT),
                                       company_name="Maruti Suzuki India")
    assert "How this hits your framework" in html and "BRSR:P6" in html


def test_email_omits_framework_section_when_absent():
    html = render_article_morning_brew(payload=_payload(), company_name="Maruti Suzuki India")
    assert "How this hits your framework" not in html            # no empty header


def test_disclaimer_states_ontology_provenance_and_drops_info_icons():
    html = render_article_morning_brew(payload=_payload(framework_hit=_BRSR_HIT),
                                       company_name="Maruti Suzuki India")
    norm = re.sub(r"\s+", " ", html)
    assert "Framework mappings come from our regulatory ontology" in norm
    assert "looked up, not guessed per article" in norm
    assert "(i) icons on each section" not in norm               # false claim removed
    assert "not investment, legal, or compliance advice" in norm  # safe-harbor kept
    assert "scenario projection" in norm                          # engine-estimate transparency kept
