"""Phase 39 — Editorial lede generator tests.

Covers:
  * `engine/analysis/lede_writer.py` — pattern dispatcher, deterministic
    fallback templates, in-memory cache, verification gate.
  * `engine/analysis/tone_guardrails.py` — `_LEDE_TONE_RULES`,
    `apply_lede_guardrails()`, `_SCORE_LEAK_PATTERNS`, extended
    `scan_for_violations` with kind="score_leak".
  * `engine/output/newsletter_morning_brew.py` — Phase 39.C renderer
    integration (lede block renders when present, absent otherwise).
  * `client` — TS interface lives in `client/src/types/index.ts` and
    is exercised via tsc -b which runs in CI.
"""
from __future__ import annotations

import os

import pytest


# ---------------------------------------------------------------------------
# tone_guardrails — Phase 39 additions
# ---------------------------------------------------------------------------


def test_apply_lede_guardrails_appends_block():
    from engine.analysis.tone_guardrails import apply_lede_guardrails

    out = apply_lede_guardrails("You are a writer.")
    assert "EDITORIAL LEDE — STRICT" in out
    assert "Mint editorial" in out
    assert "2-3 sentences" in out
    assert "named entity" in out.lower()


def test_apply_lede_guardrails_is_idempotent():
    from engine.analysis.tone_guardrails import apply_lede_guardrails

    once = apply_lede_guardrails("base")
    twice = apply_lede_guardrails(once)
    assert once == twice


def test_score_leak_detector_catches_materiality_band():
    from engine.analysis.tone_guardrails import scan_for_violations

    hits = scan_for_violations("This is HIGH materiality for the company.")
    score_leaks = [h for h in hits if h["kind"] == "score_leak"]
    assert len(score_leaks) >= 1
    assert any("materiality" in h["hit"].lower() for h in score_leaks)


def test_score_leak_detector_catches_roi_percent():
    from engine.analysis.tone_guardrails import scan_for_violations

    hits = scan_for_violations("The recommended action has ROI 400% over the cycle.")
    score_leaks = [h for h in hits if h["kind"] == "score_leak"]
    assert any("ROI" in h["hit"] for h in score_leaks)


def test_score_leak_detector_catches_payback_field():
    from engine.analysis.tone_guardrails import scan_for_violations

    hits = scan_for_violations("Owner: Head of IR, payback: 6 mo.")
    score_leaks = [h for h in hits if h["kind"] == "score_leak"]
    # Should flag both `owner:` and `payback:`
    assert len(score_leaks) >= 2


def test_score_leak_detector_passes_clean_editorial_lede():
    from engine.analysis.tone_guardrails import scan_for_violations

    clean = (
        "YES Bank posted a ₹1,068 Cr quarter on the headline. "
        "The recovery has a number behind it for the first time since 2020."
    )
    hits = scan_for_violations(clean)
    score_leaks = [h for h in hits if h["kind"] == "score_leak"]
    assert score_leaks == []


# ---------------------------------------------------------------------------
# lede_writer — module surface
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_lede_cache():
    """Reset the in-memory cache between tests so caching assertions
    don't pollute each other."""
    from engine.analysis import lede_writer
    lede_writer._LLM_LEDE_CACHE.clear()
    yield
    lede_writer._LLM_LEDE_CACHE.clear()


@pytest.fixture
def _no_llm(monkeypatch):
    """Force the deterministic-template fallback path by unsetting
    LLM keys. Used by every test that doesn't explicitly stub the LLM."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def _build_synthetic_insight(*, polarity: str, headline: str,
                              regulator: str | None = None,
                              amount_cr: float | None = None,
                              company_slug: str = "test-co",
                              event_type: str = "event_quarterly_results") -> dict:
    """Helper to build a minimal valid insight payload for the lede writer."""
    crit_summary = headline.lower()
    if regulator:
        crit_summary = f"the {regulator} ruling on {headline.lower()}"
    return {
        "article": {"id": "x", "company_slug": company_slug, "title": headline},
        "analysis": {
            "what_changed": {
                "headline": headline,
                "polarity": polarity,
                "event_type": event_type,
                "source": "Test",
                "published_at": "2026-05-27",
                "url": "https://example.com",
            },
            "why_it_matters": {
                "materiality_band": "MEDIUM",
                "criticality_summary": crit_summary,
                "financial_exposure": {"amount_cr": amount_cr} if amount_cr else {},
            },
            "what_it_triggers": {
                "frameworks": [{"code": "BRSR", "section": "P9", "is_mandatory": True}],
                "recommended_actions": [],
            },
            "what_to_watch": {
                "sentiment_trajectory": {"horizon_3m": "stable", "horizon_6m": "stable", "horizon_12m": "stable"},
            },
        },
    }


def test_write_lede_returns_empty_when_no_analysis(_no_llm):
    from engine.analysis.lede_writer import write_lede

    out = write_lede(article_id="x", insight={"article": {"id": "x"}})
    assert out == {}


def test_write_lede_returns_empty_when_no_article_id(_no_llm):
    from engine.analysis.lede_writer import write_lede

    out = write_lede(article_id="", insight=_build_synthetic_insight(
        polarity="positive", headline="x posted Q4 results", amount_cr=1000,
    ))
    assert out == {}


def test_lede_positive_event_uses_setup_twist_pattern(_no_llm):
    from engine.analysis.lede_writer import write_lede

    insight = _build_synthetic_insight(
        polarity="positive",
        headline="Test Co Q4 profit rose 45% to ₹1,000 Cr",
        amount_cr=1000.0,
    )
    out = write_lede(article_id="art1", insight=insight)
    assert out["pattern"] == "setup_twist"
    assert "₹1,000 Cr" in out["text"] or "1,000" in out["text"]
    assert out["word_count"] <= 60
    assert out["word_count"] >= 12


def test_lede_negative_event_with_regulator_uses_character_pattern(_no_llm):
    from engine.analysis.lede_writer import write_lede

    insight = _build_synthetic_insight(
        polarity="negative",
        headline="RBI Imposes ₹31.80 Lakh Penalty on Test Bank for KYC Lapses",
        regulator="RBI",
        amount_cr=53.7,
        event_type="event_regulatory_penalty",
    )
    out = write_lede(article_id="art2", insight=insight)
    assert out["pattern"] == "character"
    # Should contain the regulator name in the lede text
    assert "RBI" in out["text"]


def test_lede_neutral_event_does_not_use_action_against_framing(_no_llm):
    """A neutral disclosure event must not be framed as 'regulator action
    against the company'. This was the bug observed before the
    polarity-aware character template fix."""
    from engine.analysis.lede_writer import write_lede

    insight = _build_synthetic_insight(
        polarity="neutral",
        headline="Test Co Files SEBI Takeover Reg Disclosure on Open Market Sale",
        regulator="SEBI",
        amount_cr=50.0,
        event_type="event_compliance_filing",
    )
    out = write_lede(article_id="art3", insight=insight)
    assert out["pattern"] == "character"
    # Neutral framing — never "action against"
    assert "action against" not in out["text"].lower()
    # Should use disclosure-flavoured wording
    assert ("filed" in out["text"].lower()
            or "disclosure" in out["text"].lower())


def test_lede_generic_pattern_fires_when_no_signal(_no_llm):
    from engine.analysis.lede_writer import write_lede

    insight = _build_synthetic_insight(
        polarity="neutral",
        headline="A routine corporate update",
        amount_cr=None,
    )
    out = write_lede(article_id="art4", insight=insight)
    # No regulator, no peer comparables, no temporal markers — falls to generic
    assert out["pattern"] in ("generic", "character")
    assert out["word_count"] >= 8


def test_lede_cache_returns_cached_on_second_call(_no_llm):
    from engine.analysis.lede_writer import write_lede

    insight = _build_synthetic_insight(
        polarity="positive", headline="Test", amount_cr=500,
    )
    first = write_lede(article_id="cache-test", insight=insight)
    second = write_lede(article_id="cache-test", insight=insight)
    assert first["text"] == second["text"]
    assert second.get("cached") is True


def test_lede_force_refresh_invalidates_cache(_no_llm):
    from engine.analysis.lede_writer import write_lede

    insight = _build_synthetic_insight(
        polarity="positive", headline="Test", amount_cr=500,
    )
    first = write_lede(article_id="cache-test-2", insight=insight)
    second = write_lede(article_id="cache-test-2", insight=insight,
                        force_refresh=True)
    # Second call returns a fresh (uncached) result
    assert second.get("cached") is False


def test_lede_passes_tone_scan_clean(_no_llm):
    """Every deterministic-template lede must clear the tone scanner."""
    from engine.analysis.lede_writer import write_lede
    from engine.analysis.tone_guardrails import scan_for_violations

    polarities = [
        ("positive", "Q4 results jumped", 1500.0, None, "event_quarterly_results"),
        ("negative", "RBI penalty on bank", 25.0, "RBI", "event_regulatory_penalty"),
        ("neutral", "Company files SEBI takeover reg", 50.0, "SEBI", "event_compliance_filing"),
    ]
    for polarity, headline, amount, regulator, event in polarities:
        insight = _build_synthetic_insight(
            polarity=polarity, headline=headline, amount_cr=amount,
            regulator=regulator, event_type=event,
        )
        out = write_lede(article_id=f"scan-{polarity}", insight=insight)
        hits = scan_for_violations(out["text"])
        # Score leaks must always be empty (Phase 39 invariant)
        leaks = [h for h in hits if h["kind"] == "score_leak"]
        assert leaks == [], f"score leak on {polarity}: {leaks}"
        # Banned phrases / em-dashes also fatal
        fatal = [h for h in hits if h["kind"] in {"banned_phrase", "em_dash", "banned_opener"}]
        assert fatal == [], f"fatal tone violation on {polarity}: {fatal}"


def test_lede_word_count_under_60(_no_llm):
    """All deterministic templates must respect the 60-word cap."""
    from engine.analysis.lede_writer import write_lede

    insight = _build_synthetic_insight(
        polarity="positive", headline="Long-form quarterly result with extra context",
        amount_cr=20000,
    )
    out = write_lede(article_id="word-cap-test", insight=insight)
    assert out["word_count"] <= 60


def test_acronym_prettify_handles_known_acronyms():
    """Phase 39.A helper should preserve uppercase for known acronyms."""
    from engine.analysis.lede_writer import _prettify_slug

    assert _prettify_slug("icici-bank") == "ICICI Bank"
    assert _prettify_slug("yes-bank") == "YES Bank"
    assert _prettify_slug("jsw-energy") == "JSW Energy"
    assert _prettify_slug("hindustan-unilever-limited") == "Hindustan Unilever Limited"


# ---------------------------------------------------------------------------
# morning_brew renderer — Phase 39.C insertion
# ---------------------------------------------------------------------------


def _renderer_payload(lede_text: str | None = None) -> dict:
    """Minimal payload for the morning_brew renderer."""
    base = {
        "article": {"title": "x", "source": "Mint", "url": "https://example.com/x"},
        "insight": {
            "analysis": {
                "what_changed": {"headline": "Synthetic headline", "polarity": "neutral", "source": "Mint"},
                "why_it_matters": {
                    "materiality_band": "MEDIUM",
                    "financial_exposure": {"amount_cr": 100, "kind": "exposure"},
                    "criticality_summary": "Synthetic summary.",
                },
                "what_it_triggers": {"recommended_actions": []},
                "what_to_watch": {
                    "sentiment_trajectory": {"horizon_3m": "stable", "horizon_6m": "stable", "horizon_12m": "stable"},
                },
            },
        },
    }
    if lede_text:
        base["insight"]["analysis"]["lede"] = {
            "text": lede_text, "pattern": "generic",
            "model_used": "fallback_template",
        }
    return base


def test_renderer_emits_lede_block_when_present():
    from engine.output.newsletter_morning_brew import render_article_morning_brew

    payload = _renderer_payload(lede_text="Editorial opening sentence one. Sentence two.")
    html = render_article_morning_brew(
        payload=payload, company_name="Test Co",
        recipient_name="Rahil", company_slug="test-co", article_id="x",
    )
    # Lede uses serif italic typography (Phase 39.C signature)
    assert "font-family:Georgia" in html
    assert "font-style:italic" in html
    assert "Editorial opening sentence one" in html


def test_renderer_omits_lede_block_when_absent():
    """Back-compat invariant: pre-Phase-39 articles (no lede field) must
    render exactly the Phase 38 layout."""
    from engine.output.newsletter_morning_brew import render_article_morning_brew

    payload = _renderer_payload(lede_text=None)
    html = render_article_morning_brew(
        payload=payload, company_name="Test Co",
        recipient_name="Rahil", company_slug="test-co", article_id="x",
    )
    # No serif italic typography in the absence of a lede
    assert "font-family:Georgia" not in html
    # Layout still renders the structured sections
    assert "What changed" in html
    assert "Why it matters" in html
