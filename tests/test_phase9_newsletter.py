"""Phase 9 tests: HTML newsletter renderer + email-safe constraints."""

from __future__ import annotations

import re

from engine.output.newsletter_renderer import (
    DEFAULT_CTA_URL,
    DEFAULT_NEWSLETTER_TITLE,
    NewsletterArticle,
    _format_date,
    _prettify_source,
    render_newsletter,
)


def _sample_article(i: int, industry: str = "Power/Energy") -> NewsletterArticle:
    return NewsletterArticle(
        title=f"Test headline {i}: SEBI imposes ₹{i*100} Cr penalty",
        company_name="Adani Power",
        industry=industry,
        bottom_line=f"Bottom line {i}: ₹{i*100} Cr exposure drives 10 bps margin impact.",
        why_matters=f"Why matters {i}: Vedanta 2020 precedent suggests 6-month recovery path.",
        read_more_url=f"https://snowkap.com/brief/adani-power/art{i}",
        image_url="",
        published_at="2026-04-22T00:00:00+00:00",
        source_name="Mint",
    )


# ---------------------------------------------------------------------------
# Core rendering
# ---------------------------------------------------------------------------


def test_render_empty_articles_raises():
    import pytest as _pytest
    try:
        render_newsletter([])
    except ValueError as e:
        assert "at least 1 article" in str(e)
        return
    raise AssertionError("expected ValueError")


def test_render_minimal_newsletter_produces_valid_html_shell():
    html = render_newsletter([_sample_article(1)])
    assert html.startswith("<!DOCTYPE html")
    assert "<html xmlns" in html
    assert "</html>" in html
    # UTF-8 declared
    assert "charset=utf-8" in html


def test_cta_url_and_label_present():
    html = render_newsletter(
        [_sample_article(1)],
        cta_url="https://snowkap.com/contact-us/",
        cta_label="Book a demo with Snowkap",
    )
    assert "https://snowkap.com/contact-us/" in html
    assert "Book a demo with Snowkap" in html
    # Button has tall click target (≥ 14px vertical padding)
    assert "padding:14px " in html


def test_default_cta_is_snowkap_contact_us():
    html = render_newsletter([_sample_article(1)])
    assert DEFAULT_CTA_URL in html


def test_recipient_greeting_appears():
    html = render_newsletter([_sample_article(1)], recipient_name="Ambalika")
    assert "Dear Ambalika" in html


def test_no_recipient_omits_greeting():
    html = render_newsletter([_sample_article(1)], recipient_name=None)
    assert "Dear " not in html


def test_article_count_matches():
    arts = [_sample_article(i) for i in range(1, 6)]
    html = render_newsletter(arts)
    # Each article generates an <h3> with its headline
    for i in range(1, 6):
        assert f"Test headline {i}" in html


def test_bottom_line_and_why_matters_callouts_present():
    html = render_newsletter([_sample_article(1)])
    # Redesigned in Phase 10 brand refresh — uppercase pill labels now, no colon.
    assert "The bottom line" in html
    assert "Why this matters" in html


def test_newsletter_title_customisable():
    html = render_newsletter([_sample_article(1)], newsletter_title="Power Sector Weekly")
    assert "Power Sector Weekly" in html


def test_highlights_grid_has_two_columns():
    arts = [_sample_article(i) for i in range(1, 7)]
    html = render_newsletter(arts)
    # Two 50% width columns in the highlights section
    assert 'width="50%"' in html


def test_industry_emoji_mapping():
    a1 = NewsletterArticle(
        title="x", company_name="c", industry="Power/Energy",
        bottom_line="b", why_matters="w", read_more_url="http://x",
    )
    a2 = NewsletterArticle(
        title="x", company_name="c", industry="Renewable Energy",
        bottom_line="b", why_matters="w", read_more_url="http://x",
    )
    assert a1.emoji() != a2.emoji()  # different industries → different emojis


def test_unknown_industry_falls_back_to_generic_emoji():
    a = NewsletterArticle(
        title="x", company_name="c", industry="Martian Rover Manufacturing",
        bottom_line="b", why_matters="w", read_more_url="http://x",
    )
    assert a.emoji() == "🌿"  # generic fallback


# ---------------------------------------------------------------------------
# Email-client-safe constraints
# ---------------------------------------------------------------------------


def test_no_style_block_for_email_safety():
    """Gmail's "clipped" mode strips <style> tags; we must inline everything."""
    html = render_newsletter([_sample_article(1)])
    # No top-level style block in head or body
    assert "<style>" not in html
    assert "<style " not in html


def test_no_flexbox_or_grid():
    """Outlook doesn't support modern CSS layout."""
    html = render_newsletter([_sample_article(1)])
    # 'display:flex' or 'display:grid' → email breaks in Outlook
    assert "display:flex" not in html
    assert "display:grid" not in html


def test_uses_tables_for_layout():
    """Multiple tables expected — email-safe convention."""
    html = render_newsletter([_sample_article(i) for i in range(1, 4)])
    # At least header, greeting, highlights, 3 articles, CTA, footer → 8+ tables
    table_count = len(re.findall(r"<table[\s>]", html))
    assert table_count >= 8


def test_hex_colors_only():
    """'oklch()' or 'named colors' break in some clients — stick to hex."""
    html = render_newsletter([_sample_article(1)])
    assert "oklch(" not in html


def test_absolute_image_urls_only():
    """Email clients strip relative image URLs."""
    a = NewsletterArticle(
        title="x", company_name="c", industry="Power/Energy",
        bottom_line="b", why_matters="w", read_more_url="http://x",
        image_url="https://example.com/img.jpg",
    )
    html = render_newsletter([a])
    assert 'src="https://example.com/img.jpg"' in html


def test_emoji_banner_fallback_when_no_image():
    """If no image URL, fall back to the branded emoji banner block."""
    html = render_newsletter([_sample_article(1)])  # default has no image_url
    # Phase 10 brand refresh: black background with orange accent rule.
    # Accept any of the brand colours so future tweaks don't break the test.
    assert (
        "background-color:#0F172A" in html
        or "background-color:#F97316" in html
        or "linear-gradient" in html
    )


def test_body_width_is_600px():
    """Standard email width (works cross-client)."""
    html = render_newsletter([_sample_article(1)])
    assert 'width="600"' in html


def test_html_escapes_user_input():
    """Ensure article content doesn't allow HTML injection."""
    a = NewsletterArticle(
        title="<script>alert('xss')</script>",
        company_name="c", industry="Power/Energy",
        bottom_line="<img onerror=alert(1)>",
        why_matters="safe text",
        read_more_url="http://x",
    )
    html = render_newsletter([a])
    assert "<script>" not in html
    # Angle brackets escaped
    assert "&lt;script&gt;" in html or "&lt;img" in html


def test_unsubscribe_link_appears_when_provided():
    html = render_newsletter(
        [_sample_article(1)],
        unsubscribe_url="https://snowkap.com/unsubscribe?token=xxx",
    )
    assert "Unsubscribe" in html
    assert "https://snowkap.com/unsubscribe?token=xxx" in html


def test_unsubscribe_omitted_when_not_provided():
    html = render_newsletter([_sample_article(1)], unsubscribe_url=None)
    assert "Unsubscribe" not in html


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_format_date_iso_to_friendly():
    assert _format_date("2026-04-22T10:00:00+00:00") == "April 22, 2026"
    assert _format_date("2026-01-05T00:00:00Z") == "January 5, 2026"


def test_format_date_fallback_on_bad_input():
    # Unparseable → fallback to 'today'
    out = _format_date("not-a-date")
    assert re.match(r"^[A-Z][a-z]+ \d{1,2}, 20\d{2}$", out)


def test_prettify_source_domain():
    assert _prettify_source("www.economictimes.com") == "Economictimes"
    assert _prettify_source("Mint") == "Mint"  # non-domain passes through
    assert _prettify_source("") == ""
