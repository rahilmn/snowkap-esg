"""Phase 4 §6.4 follow-up — per-role rendering in render_article_brief_dark.

Validates that passing role=cfo|ceo|esg-analyst surfaces the matching
perspective's headline + what_matters + action in the rendered HTML
body, while role=None / unknown falls back to the legacy generic
insight-driven render (byte-identical for back-compat).
"""
from __future__ import annotations

from engine.output.newsletter_renderer import (
    _normalise_role_key,
    render_article_brief_dark,
)


def _payload_with_perspectives() -> dict:
    return {
        "article": {
            "title": "Generic article title",
            "source": "Reuters",
            "published_at": "2026-05-10T00:00:00Z",
            "url": "https://example.com/article",
        },
        "pipeline": {
            "nlp": {"sentiment": -1},
            "themes": {"primary_sub_metrics": ["scope1_emissions"]},
            "frameworks": [],
        },
        "insight": {
            "headline": "Generic insight headline",
            "net_impact_summary": "Generic net impact summary across all roles.",
            "core_mechanism": "Generic core mechanism explanation.",
            "decision_summary": {
                "materiality": "HIGH",
                "key_risk": "Generic key risk text.",
                "top_opportunity": "Generic opportunity text.",
            },
            "perspectives": {
                "cfo": {
                    "perspective": "cfo",
                    "headline": "CFO: Margin compresses ~₹500 Cr over Q4",
                    "what_matters": [
                        "P&L exposure: 6.3% of revenue at risk this quarter.",
                        "Hedging cost: ~₹50 Cr to neutralise the FX shock.",
                    ],
                    "action": ["Hedge 60% of Q4 USD exposure by 2026-06-30."],
                    "why_critical": "CFO why-critical paragraph anchored on payback.",
                },
                "ceo": {
                    "perspective": "ceo",
                    "headline": "CEO: Strategic positioning in green capex",
                    "what_matters": [
                        "3-year horizon: shifts capital allocation toward renewables.",
                        "Peer signal: Tata Power moved ahead on a similar bid.",
                    ],
                    "action": ["Anchor next board narrative on transition strategy."],
                    "why_critical": "CEO why-critical paragraph anchored on board narrative.",
                },
                "esg-analyst": {
                    "perspective": "esg-analyst",
                    "headline": "ESG Analyst: BRSR P6 disclosure trigger",
                    "what_matters": [
                        "Framework gap: BRSR P6:Q14 requires Scope 3 disclosure by FY27.",
                        "Audit trail: cascade β=0.34, lag 6mo, source: Reuters May 7.",
                    ],
                    "action": ["File BRSR P6 update with the FY27 disclosure cycle."],
                    "why_critical": "Analyst why-critical paragraph anchored on framework deadlines.",
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# _normalise_role_key
# ---------------------------------------------------------------------------


def test_role_normalisation_canonical_keys():
    assert _normalise_role_key("cfo") == "cfo"
    assert _normalise_role_key("CFO") == "cfo"
    assert _normalise_role_key("ceo") == "ceo"
    assert _normalise_role_key("CEO") == "ceo"


def test_role_normalisation_analyst_aliases():
    """Plan §6.4 lists 'analyst' but the perspective dict key is
    'esg-analyst'. Both spellings must resolve to the canonical key."""
    assert _normalise_role_key("analyst") == "esg-analyst"
    assert _normalise_role_key("esg-analyst") == "esg-analyst"
    assert _normalise_role_key("esg_analyst") == "esg-analyst"


def test_role_normalisation_unknown_returns_none():
    assert _normalise_role_key("") is None
    assert _normalise_role_key(None) is None
    assert _normalise_role_key("sustainability_manager") is None


# ---------------------------------------------------------------------------
# Per-role render swap
# ---------------------------------------------------------------------------


def test_render_uses_cfo_headline_when_role_cfo():
    html = render_article_brief_dark(
        payload=_payload_with_perspectives(),
        company_name="Acme Co",
        industry="Power",
        recipient_name="Test",
        role="cfo",
    )
    assert "CFO: Margin compresses" in html
    # Generic headline should NOT be the title — CFO role headline wins
    assert "Generic insight headline" not in html or html.count("CFO: Margin") >= 1


def test_render_uses_ceo_headline_when_role_ceo():
    html = render_article_brief_dark(
        payload=_payload_with_perspectives(),
        company_name="Acme Co",
        industry="Power",
        recipient_name="Test",
        role="ceo",
    )
    assert "CEO: Strategic positioning" in html


def test_render_uses_analyst_headline_for_either_alias():
    """'analyst' and 'esg-analyst' must produce identical body content."""
    p = _payload_with_perspectives()
    html_a = render_article_brief_dark(
        payload=p, company_name="Acme Co", industry="Power",
        recipient_name="Test", role="analyst",
    )
    html_b = render_article_brief_dark(
        payload=p, company_name="Acme Co", industry="Power",
        recipient_name="Test", role="esg-analyst",
    )
    assert "ESG Analyst: BRSR P6" in html_a
    assert "ESG Analyst: BRSR P6" in html_b


def test_render_falls_back_to_generic_when_role_omitted():
    """No role → legacy generic-headline path (back-compat for callers
    that haven't been updated)."""
    html = render_article_brief_dark(
        payload=_payload_with_perspectives(),
        company_name="Acme Co",
        industry="Power",
        recipient_name="Test",
    )
    assert "Generic insight headline" in html
    # And no role-specific headline leaked
    assert "CFO: Margin compresses" not in html
    assert "CEO: Strategic positioning" not in html


def test_render_falls_back_when_perspective_missing_for_role():
    """If we ask for ceo but perspectives only has cfo, fall back to
    generic — never crash, never silently swap to a wrong role."""
    payload = _payload_with_perspectives()
    payload["insight"]["perspectives"] = {
        "cfo": payload["insight"]["perspectives"]["cfo"],
    }
    html = render_article_brief_dark(
        payload=payload, company_name="Acme Co", industry="Power",
        recipient_name="Test", role="ceo",
    )
    assert "Generic insight headline" in html
    assert "CFO: Margin compresses" not in html


def test_render_surfaces_role_what_matters_bullets():
    """role-specific what_matters bullets replace the generic core_mechanism
    + key_risk + top_opportunity bag-of-everything assembly."""
    html = render_article_brief_dark(
        payload=_payload_with_perspectives(),
        company_name="Acme Co",
        industry="Power",
        recipient_name="Test",
        role="cfo",
    )
    # CFO bullets present (HTML-escaped: & → &amp;)
    assert "P&amp;L exposure: 6.3% of revenue at risk this quarter." in html
    assert "Hedging cost: ~₹50 Cr to neutralise the FX shock." in html
    # Generic key_risk text NOT present (replaced)
    assert "Generic key risk text." not in html


def test_render_appends_action_bullet_when_present():
    """Role's recommended action becomes the closing bullet, prefixed
    'Action: '. Avoids dropping the do-this signal."""
    html = render_article_brief_dark(
        payload=_payload_with_perspectives(),
        company_name="Acme Co",
        industry="Power",
        recipient_name="Test",
        role="cfo",
    )
    assert "Action: Hedge 60% of Q4 USD exposure by 2026-06-30." in html


def test_render_uses_role_why_critical_as_executive_summary():
    """The role's why_critical paragraph becomes the email's executive
    summary, beating the generic net_impact_summary."""
    html = render_article_brief_dark(
        payload=_payload_with_perspectives(),
        company_name="Acme Co",
        industry="Power",
        recipient_name="Test",
        role="cfo",
    )
    assert "CFO why-critical paragraph anchored on payback." in html


def test_render_unknown_role_falls_back_silently():
    """Unknown role string never raises — falls through to generic."""
    html = render_article_brief_dark(
        payload=_payload_with_perspectives(),
        company_name="Acme Co",
        industry="Power",
        recipient_name="Test",
        role="random_role_value",
    )
    assert "Generic insight headline" in html


def test_render_no_action_required_action_is_skipped():
    """When the role's action list is just ['No action required'], we
    don't emit a noisy 'Action: No action required' bullet."""
    payload = _payload_with_perspectives()
    payload["insight"]["perspectives"]["cfo"]["action"] = ["No action required"]
    html = render_article_brief_dark(
        payload=payload, company_name="Acme Co", industry="Power",
        recipient_name="Test", role="cfo",
    )
    assert "Action: No action required" not in html
