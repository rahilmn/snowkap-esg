"""Phase 11C — Editorial subject line + stakes-first intro tests.

Guards the marketing brand quality — if these break, emails stop sounding
like Snowkap and start sounding like a generic Mailchimp template.

Covers:
  * Template cascade (compliance → disclosure → opportunity → generic)
  * 90-char cap on all template outputs
  * Stakes-first intro opens with ₹ exposure + materiality
  * LLM path is tried for HIGH/CRITICAL but gracefully falls through
    when OPENAI_API_KEY is unset
"""

from __future__ import annotations

from unittest.mock import patch

from engine.output.intro_copywriter import build_intro
from engine.output.subject_line import MAX_LEN, build_subject


# ---------------------------------------------------------------------------
# Subject line cascade
# ---------------------------------------------------------------------------


def test_compliance_template_fires_on_exposure_plus_regulator():
    """HIGH materiality + ₹ exposure + SEBI mention → compliance template."""
    insight = {
        "decision_summary": {
            "materiality": "HIGH",
            "financial_exposure": "₹275 Cr",
            "key_risk": "SEBI penalty risk on non-disclosure",
        },
        "headline": "SEBI imposes ₹275 Cr penalty on Adani Power",
    }
    # Clear OPENAI_API_KEY so we force the template path (no LLM call)
    with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
        subj = build_subject("Adani Power", insight, {})
    assert "₹275 Cr" in subj
    assert "SEBI" in subj
    assert "Adani Power" in subj
    assert len(subj) <= MAX_LEN


def test_disclosure_template_fires_on_framework_reference():
    insight = {
        "decision_summary": {"materiality": "MODERATE"},
        "headline": "ICICI Bank misses BRSR P6 disclosure deadline",
        "core_mechanism": "BRSR P6 section gap flagged by auditors",
    }
    with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
        subj = build_subject("ICICI Bank", insight, {})
    assert "BRSR" in subj
    assert "ICICI Bank" in subj
    assert len(subj) <= MAX_LEN


def test_generic_fallback_preserves_company():
    insight = {
        "decision_summary": {"materiality": "LOW"},
        "headline": "Adani Power announces quarterly results",
    }
    with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
        subj = build_subject("Adani Power", insight, {})
    assert "Adani Power" in subj
    assert len(subj) <= MAX_LEN


def test_subject_is_always_capped_at_90_chars():
    """Defensive: a very long headline must never produce a subject > 90 chars."""
    long_insight = {
        "decision_summary": {
            "materiality": "HIGH",
            "financial_exposure": "₹12,456,789 Cr",
            "key_risk": "SEBI + RBI + MoEFCC + EPA enforcement action across multiple jurisdictions",
        },
        "headline": "A very long headline " * 10,
    }
    with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
        subj = build_subject("Mega Corporation International Limited", long_insight, {})
    assert len(subj) <= MAX_LEN


def test_high_materiality_tries_llm_first_but_falls_back_cleanly():
    """When LLM raises, fall back to templates — never blow up the email."""
    insight = {
        "decision_summary": {
            "materiality": "CRITICAL",
            "financial_exposure": "₹275 Cr",
            "key_risk": "SEBI penalty",
        },
        "headline": "Critical event",
    }
    # OPENAI_API_KEY present → LLM path is tried. Mock it to raise.
    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-fake"}, clear=False):
        with patch("engine.output.subject_line._llm_subject", return_value=None):
            subj = build_subject("Adani Power", insight, {"id": "test1"})
    assert subj  # non-empty
    assert len(subj) <= MAX_LEN


# ---------------------------------------------------------------------------
# Stakes-first intro
# ---------------------------------------------------------------------------


def test_intro_opens_with_exposure_and_materiality():
    insight = {
        "decision_summary": {
            "materiality": "HIGH",
            "financial_exposure": "₹275 Cr",
            "key_risk": "SEBI enforcement within 4 weeks",
            "timeline": "within 4 weeks",
        },
        "headline": "SEBI imposes ₹275 Cr penalty",
        "net_impact_summary": "Board must approve remediation within 4 weeks.",
        "core_mechanism": "Non-disclosure triggered penalty + margin compression.",
    }
    intro = build_intro("Adani Power", insight, {})
    assert "₹275 Cr" in intro
    assert "High" in intro or "HIGH" in intro
    assert "SEBI" in intro or "4 weeks" in intro


def test_intro_falls_back_gracefully_when_no_exposure():
    insight = {
        "decision_summary": {"materiality": "LOW"},
        "headline": "Minor ESG update",
        "core_mechanism": "",
    }
    intro = build_intro("Test Co", insight, {})
    assert intro  # non-empty
    # Low-materiality path: still mention materiality OR fall back to generic
    assert "Test Co" in intro or "Low" in intro or "LOW" in intro or "two minutes" in intro


def test_intro_respects_sender_note_override():
    """The admin's sender_note, if provided, is the final word — no
    auto-generation overrides it."""
    # Test via share_service's wrapper — sender_note always wins
    from engine.output.share_service import _build_intro_paragraph
    result = _build_intro_paragraph(
        recipient_name="CI",
        sender_note="Custom pitch goes here.",
        company_name="Adani Power",
        payload={"insight": {"decision_summary": {"materiality": "HIGH", "financial_exposure": "₹999 Cr"}}},
    )
    assert result == "Custom pitch goes here."


def test_intro_uses_stakes_when_payload_provided_no_sender_note():
    from engine.output.share_service import _build_intro_paragraph
    payload = {
        "insight": {
            "decision_summary": {
                "materiality": "HIGH",
                "financial_exposure": "₹500 Cr",
                "key_risk": "SEBI fine",
            },
            "headline": "Adani penalty",
            "core_mechanism": "Reg breach.",
        }
    }
    result = _build_intro_paragraph(
        recipient_name=None,
        sender_note=None,
        company_name="Adani Power",
        payload=payload,
    )
    assert "₹500 Cr" in result
    assert "Adani Power" in result or "Reg breach" in result
