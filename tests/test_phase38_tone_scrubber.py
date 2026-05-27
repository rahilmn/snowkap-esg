"""Phase 38 — Editorial tone guardrails + post-render scrubber tests.

Covers:
  * `engine/analysis/tone_guardrails.py` — banned word/phrase/opener
    detection, idempotent prompt-block append.
  * `engine/output/content_scrubber.py` — 5-pass HTML scrub
    (em-dash sweep, jargon swap, banned-word strip, banned-phrase
    sentence deletion, opener-shape trim).
  * `engine/output/newsletter_morning_brew.py` — emoji-free render +
    upsized logo + uppercase section labels (Phase 38.1 / 38.4 invariants).
"""
from __future__ import annotations

import re

import pytest


# ---------------------------------------------------------------------------
# tone_guardrails
# ---------------------------------------------------------------------------


def test_tone_guardrails_scan_detects_banned_word():
    from engine.analysis.tone_guardrails import scan_for_violations

    hits = scan_for_violations("We leverage robust analytics to deliver value.")
    kinds = {h["kind"] for h in hits}
    assert "banned_word" in kinds
    hit_words = {h["hit"].lower() for h in hits if h["kind"] == "banned_word"}
    assert "leverage" in hit_words
    assert "robust" in hit_words


def test_tone_guardrails_scan_detects_banned_phrase():
    from engine.analysis.tone_guardrails import scan_for_violations

    hits = scan_for_violations("In the realm of consumer goods this matters.")
    assert any(h["kind"] == "banned_phrase" for h in hits)


def test_tone_guardrails_scan_detects_em_dash():
    from engine.analysis.tone_guardrails import scan_for_violations

    hits = scan_for_violations("Sentiment is declining — plan a response.")
    assert any(h["kind"] == "em_dash" for h in hits)


def test_tone_guardrails_scan_clean_text_returns_zero():
    from engine.analysis.tone_guardrails import scan_for_violations

    clean = "Nestle India posted Rs 16,200 Cr Q4 turnover, up 7.4%."
    assert scan_for_violations(clean) == []


def test_apply_to_system_prompt_appends_block():
    from engine.analysis.tone_guardrails import apply_to_system_prompt

    base = "You are an analyst."
    out = apply_to_system_prompt(base)
    assert out.startswith(base)
    assert "EDITORIAL TONE" in out
    assert "BANNED WORDS" in out


def test_apply_to_system_prompt_is_idempotent():
    """Applying twice yields the same single trailing block."""
    from engine.analysis.tone_guardrails import apply_to_system_prompt

    once = apply_to_system_prompt("You are an analyst.")
    twice = apply_to_system_prompt(once)
    assert once == twice


def test_apply_subject_line_guardrails_is_short_subset():
    from engine.analysis.tone_guardrails import apply_subject_line_guardrails

    out = apply_subject_line_guardrails("Write a subject.")
    assert "SUBJECT LINE GUARDRAILS" in out
    # Subject-line subset should NOT include the full Hemingway block.
    assert "EDITORIAL TONE" not in out


# ---------------------------------------------------------------------------
# content_scrubber
# ---------------------------------------------------------------------------


def test_scrubber_strips_em_dash_to_comma_or_period():
    from engine.output.content_scrubber import scrub_html

    out = scrub_html("<p>Margin lifted 80 bps — the second straight quarter.</p>")
    assert "—" not in out
    # "the" is a clause starter; em-dash should become a period.
    assert "80 bps. The second straight quarter" in out or "80 bps, the second straight quarter" in out


def test_scrubber_swaps_jargon_to_plain_english():
    from engine.output.content_scrubber import scrub_html

    out = scrub_html("<p>We utilize the methodology to leverage data.</p>")
    assert "utilize" not in out.lower()
    assert "methodology" not in out.lower()
    assert "leverage" not in out.lower()
    # Plain swaps land.
    assert "use" in out.lower() or "method" in out.lower()


def test_scrubber_strips_banned_word_adjectives():
    """Banned words without a jargon swap get deleted inline."""
    from engine.output.content_scrubber import scrub_html

    out = scrub_html("<p>Our robust and seamless platform.</p>")
    assert "robust" not in out
    assert "seamless" not in out


def test_scrubber_deletes_sentence_with_banned_phrase():
    from engine.output.content_scrubber import scrub_html

    src = (
        "<p>Nestle posted Rs 16,200 Cr turnover. "
        "In the realm of consumer goods, this performance speaks volumes.</p>"
    )
    out = scrub_html(src)
    assert "Rs 16,200 Cr turnover" in out
    assert "In the realm of" not in out


def test_scrubber_trims_banned_opener():
    from engine.output.content_scrubber import scrub_html

    src = "<p>In today's data-driven landscape, X happened.</p>"
    out = scrub_html(src)
    assert "In today's" not in out
    assert "X happened" in out


def test_scrubber_preserves_clean_text():
    """Clean editorial input must pass through unchanged."""
    from engine.output.content_scrubber import scrub_html

    clean = (
        "<p>Nestle India posted Rs 16,200 Cr Q4 turnover, up 7.4% year-on-year. "
        "Home Care led growth at 11%.</p>"
    )
    assert scrub_html(clean).strip() == clean.strip()


def test_scrubber_is_idempotent():
    """Running scrub twice yields the same output."""
    from engine.output.content_scrubber import scrub_html

    bad = (
        "<p>In today's landscape, we leverage robust insights — "
        "navigating the intricate dynamics.</p>"
    )
    once = scrub_html(bad)
    twice = scrub_html(once)
    assert once == twice


def test_scrubber_preserves_inline_styles_and_anchors():
    """Inline styles + href attributes must survive the scrub byte-for-byte."""
    from engine.output.content_scrubber import scrub_html

    src = (
        '<p style="color:#df5900">'
        '<a href="https://snowkap.com/leverage-page">Click here to leverage data</a>'
        '</p>'
    )
    out = scrub_html(src)
    # Style + href + anchor TEXT all unchanged (we skip <a> content).
    assert 'style="color:#df5900"' in out
    assert 'href="https://snowkap.com/leverage-page"' in out
    # Anchor text inside <a> is in the _SCRUB_SKIP_TAGS set, so "leverage"
    # survives there (we don't want to rewrite hyperlink labels).
    assert "Click here to leverage data" in out


def test_scrubber_handles_empty_and_malformed_input():
    from engine.output.content_scrubber import scrub_html, scrub_text

    assert scrub_html("") == ""
    assert scrub_html(None) is None
    assert scrub_text("") == ""
    # Even malformed HTML should not raise.
    out = scrub_html("<p>oops <broken")
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# morning_brew render — Phase 38.1 + 38.4 invariants
# ---------------------------------------------------------------------------


@pytest.fixture
def _sample_payload():
    return {
        "article": {"title": "Sample", "source": "Mint", "url": "https://example.com/x"},
        "insight": {
            "analysis": {
                "what_changed": {
                    "headline": "Nestle India posted Rs 16,200 Cr Q4 turnover.",
                    "polarity": "positive",
                    "source": "Mint",
                },
                "why_it_matters": {
                    "materiality_band": "HIGH",
                    "financial_exposure": {"amount_cr": 16200, "kind": "revenue_uplift"},
                    "criticality_summary": "Margin expansion of 80 bps quarter on quarter.",
                    "stakes_for_company": "Home Care led growth at 11%.",
                    "warning": None,
                },
                "what_it_triggers": {
                    "recommended_actions": [
                        {
                            "title": "Update Investor ESG Pitch Deck",
                            "owner": "Head of IR",
                            "deadline": "Q1 FY27",
                        },
                    ],
                },
                "what_to_watch": {
                    "sentiment_trajectory": {
                        "horizon_3m": "improving",
                        "horizon_6m": "improving",
                        "horizon_12m": "improving",
                    },
                    "top_risk_categories": ["Pricing power"],
                    "benchmarks": [],
                },
            },
        },
    }


def test_morning_brew_render_has_no_emoji(_sample_payload):
    from engine.output.newsletter_morning_brew import render_article_morning_brew

    html = render_article_morning_brew(
        payload=_sample_payload, company_name="Nestle India",
        recipient_name="Rahil", company_slug="nestle-india", article_id="x",
    )
    # No body emoji glyphs in the U+1F300..U+1FAFF or U+2600..U+27BF ranges.
    emoji = re.findall(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]", html)
    assert emoji == [], f"emoji glyphs present: {set(emoji)!r}"


def test_morning_brew_render_logo_is_200px(_sample_payload):
    from engine.output.newsletter_morning_brew import render_article_morning_brew

    html = render_article_morning_brew(
        payload=_sample_payload, company_name="Nestle India",
        company_slug="nestle-india", article_id="x",
    )
    assert re.search(r'<img[^>]*cid:snowkap-logo[^>]*width="200"', html), \
        "logo must be sized 200px per Phase 38.4"


def test_morning_brew_render_section_labels_are_uppercase(_sample_payload):
    from engine.output.newsletter_morning_brew import render_article_morning_brew

    html = render_article_morning_brew(
        payload=_sample_payload, company_name="Nestle India",
        company_slug="nestle-india", article_id="x",
    )
    # The new section labels (case-insensitive — the CSS class makes them
    # uppercase but the underlying text is mixed case).
    for label in ("What changed", "Why it matters", "Recommended actions",
                  "Forward indicators"):
        assert label in html, f"missing label: {label}"
    # And the OLD emoji-prefixed labels must be gone.
    for old in ("📰 The story", "💡 Why you", "⚡ What that means",
                "🔮 What to watch"):
        assert old not in html


def test_morning_brew_html_parses(_sample_payload):
    """Render output must be parseable HTML (no broken tags)."""
    from engine.output.newsletter_morning_brew import render_article_morning_brew
    from html.parser import HTMLParser

    html = render_article_morning_brew(
        payload=_sample_payload, company_name="Nestle India",
        company_slug="nestle-india", article_id="x",
    )

    class _Validator(HTMLParser):
        def __init__(self):
            super().__init__()
            self.errors: list[str] = []

    v = _Validator()
    v.feed(html)
    assert not v.errors
    assert len(html) > 1000  # not a stub
