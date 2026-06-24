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


@pytest.mark.parametrize("phrase", [
    "MSCI ESG rating: BBB",
    "CRISIL ESG score 61",
    "DJSI Emerging Markets reviews ratings in October",
    "Sustainalytics risk score moved from high to low",
    "ISS QualityScore on governance improved",
    "S&P Global ESG places the company in top quartile",
    "Refinitiv ESG composite score above 80",
    "ESG rating: AA on the latest cycle",
    "ESG score 61 puts it third in the peer group",
])
def test_third_party_rating_bureaus_are_blocked(phrase):
    """Phase 39 polish (2026-05-27) — user-stated rule: no third-party
    rating bureau mentions in any user-facing prose. Frameworks like
    BRSR / GRI / CSRD / TCFD remain allowed (disclosure obligations,
    not opinion scores)."""
    from engine.analysis.tone_guardrails import scan_for_violations

    hits = [h for h in scan_for_violations(phrase) if h["kind"] == "score_leak"]
    assert hits, f"expected score_leak hit for {phrase!r}"


@pytest.mark.parametrize("phrase", [
    "The disclosure aligns with BRSR Principle 9 reporting",
    "GRI 207 tax-transparency framework",
    "CSRD Article 19a applies in the EU",
    "TCFD Strategy-c disclosure due Q3",
    "ESG rating methodology under public consultation",  # methodology context
    "Rating reviewed by the regulator's working group",  # no specific bureau
])
def test_framework_citations_are_allowed(phrase):
    """Frameworks (BRSR / GRI / CSRD / TCFD / SEBI Takeover Reg) are
    disclosure obligations and remain citable. Only opinion-scoring
    bureaus (MSCI / CRISIL / DJSI / Sustainalytics / ISS / S&P / Refinitiv)
    are stripped."""
    from engine.analysis.tone_guardrails import scan_for_violations

    hits = [h for h in scan_for_violations(phrase) if h["kind"] == "score_leak"]
    assert hits == [], f"unexpected score_leak hits on {phrase!r}: {hits}"


def test_renderer_omits_external_benchmarks_section():
    """Phase 39 polish — the morning_brew renderer must not surface
    company_benchmarks data (MSCI / CRISIL / DJSI / Sustainalytics)
    even when the benchmarks field on the analysis block is populated.
    Data still lives in SQLite for future analyst-mode use."""
    from engine.output.newsletter_morning_brew import render_article_morning_brew

    payload = {
        "article": {"title": "x", "source": "Mint", "url": "https://example.com"},
        "insight": {
            "analysis": {
                "what_changed": {"headline": "x", "polarity": "positive", "source": "Mint"},
                "why_it_matters": {
                    "materiality_band": "HIGH",
                    "financial_exposure": {"amount_cr": 100, "kind": "exposure"},
                    "criticality_summary": "y",
                },
                "what_it_triggers": {"recommended_actions": []},
                "what_to_watch": {
                    "sentiment_trajectory": {"horizon_3m": "stable", "horizon_6m": "stable", "horizon_12m": "stable"},
                    "benchmarks": [
                        {"source": "MSCI ESG", "metric": "rating", "value": "BBB"},
                        {"source": "CRISIL", "metric": "esg_score", "value": "61"},
                        {"source": "DJSI", "metric": "inclusion", "value": "Yes"},
                    ],
                },
            },
        },
    }
    html = render_article_morning_brew(
        payload=payload, company_name="Test Co",
        recipient_name="Rahil", company_slug="test-co", article_id="x",
    )
    # None of the bureau names should appear in the rendered email
    assert "MSCI ESG" not in html
    assert "CRISIL" not in html
    assert "DJSI" not in html
    assert "External benchmarks" not in html


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


# ---------------------------------------------------------------------------
# Phase 40.A — lede article-body grounding
# ---------------------------------------------------------------------------


def test_grounding_rejects_invented_peer_company():
    """The PUMA hallucination bug: lede claimed 'Mark Langer steered
    Hugo Boss through ESRS reporting' on an article that contained
    zero mention of Hugo Boss. Verifier must reject this."""
    from engine.analysis.lede_writer import _verify_lede

    bad_lede = (
        "PUMA named Mark Langer as Chief Financial Officer on 30 April. "
        "Langer built his reputation steering Hugo Boss through ESRS reporting "
        "before its 2024 turnaround. The board now treats sustainability as a "
        "finance function."
    )
    article_body = (
        "PUMA SE today announced the appointment of Mark Langer as Chief "
        "Financial Officer, effective 30 April 2026. Langer joins the "
        "executive board to oversee finance and controlling."
    )
    passed, reason = _verify_lede(bad_lede, article_body=article_body, company_name="PUMA SE")
    assert not passed
    assert "ungrounded" in reason
    assert "hugo" in reason.lower() or "boss" in reason.lower()


def test_grounding_passes_when_lede_is_article_grounded():
    """Lede that only mentions facts present in the article body
    should clear the grounding check."""
    from engine.analysis.lede_writer import _verify_lede

    clean = (
        "PUMA SE named Mark Langer as Chief Financial Officer on 30 April. "
        "Langer joins the executive board to oversee finance. "
        "The appointment closes the CFO succession opened by Hubert Hinterseher."
    )
    article_body = (
        "PUMA SE today announced the appointment of Mark Langer as Chief "
        "Financial Officer, effective 30 April 2026. Langer joins the "
        "executive board to oversee finance and controlling. The company "
        "thanked outgoing CFO Hubert Hinterseher for his contribution."
    )
    passed, reason = _verify_lede(clean, article_body=article_body, company_name="PUMA SE")
    assert passed, f"clean lede rejected for reason: {reason}"


def test_grounding_allows_whitelisted_regulators_and_frameworks():
    """Regulators (RBI, SEBI, SEC, MSCI, etc.) and frameworks (BRSR,
    GRI, CSRD) are allowed in the lede even when not in the article
    body — they're institutional context, not invented entities."""
    from engine.analysis.lede_writer import _verify_lede

    lede = (
        "YES Bank received an RBI penalty of ₹31.80 lakh this Friday. "
        "The notice cites BRSR Principle 9 governance lapses. "
        "It is the third KYC penalty this calendar year."
    )
    article_body = (
        "The Reserve Bank of India has imposed a monetary penalty of "
        "Rs 31.80 lakh on YES Bank for non-compliance with KYC norms."
    )
    passed, _ = _verify_lede(lede, article_body=article_body, company_name="YES Bank")
    # Allowed even though "BRSR" + "Principle" don't appear in the body
    # — RBI is article-grounded, BRSR is a whitelist framework.
    assert passed


def test_grounding_skips_when_article_body_empty():
    """When article body is empty/missing (headline-only), skip the
    grounding check — there's nothing to check against."""
    from engine.analysis.lede_writer import _verify_lede

    lede = (
        "Test Company posted ₹500 Cr Q4 turnover. "
        "Operating margin expanded to 14%. The cycle has turned."
    )
    passed, _ = _verify_lede(lede, article_body="", company_name="Test Company")
    assert passed  # No body → skip grounding


# ---------------------------------------------------------------------------
# Phase 54 — empty/thin-body lede must not store the model's refusal text
# ---------------------------------------------------------------------------
# Live bug: on an adani-power deck card (a Tripura 11 MW solar article with an
# empty body) the reasoning model returned its own fallback REASONING instead of
# a lede — "The article body excerpt is empty. … A deterministic fallback is the
# correct output here: …" — and it got stored verbatim as the lede. Two guards
# now prevent this: (a) skip the LLM when the body is too thin to ground; and
# (b) reject refusal/meta-commentary in `_verify_lede` as a backstop.


# The exact shape of the broken lede observed in production.
_REFUSAL_LEDE = (
    "The article body excerpt is empty. With no facts to ground a lede, "
    "fabricating context would violate the hard grounding rules. A "
    "deterministic fallback is the correct output here: Tripura announced "
    "an 11 MW solar push on 22 June 2026."
)


def test_looks_like_refusal_detects_meta_commentary():
    from engine.analysis.lede_writer import _looks_like_refusal

    assert _looks_like_refusal(_REFUSAL_LEDE)
    assert _looks_like_refusal("I cannot write a lede without article facts.")
    # A clean editorial lede must NOT trip the detector.
    assert not _looks_like_refusal(
        "Adani Power switched on 11 MW of Tripura solar this week. "
        "The capacity is small. The signal on the transition is not."
    )


def test_verify_lede_rejects_refusal_meta_commentary():
    """The refusal text is the right length and cites only grounded
    entities (Tripura), so the proper-noun + length checks pass — the
    dedicated refusal guard is what must reject it."""
    from engine.analysis.lede_writer import _verify_lede

    passed, reason = _verify_lede(
        _REFUSAL_LEDE, article_body="", company_name="Adani Power",
    )
    assert not passed
    assert reason == "refusal_meta_commentary"


def test_empty_body_skips_llm_and_uses_template(monkeypatch):
    """Guard (a): an empty article body must short-circuit to the
    deterministic template — the LLM is never called, so its refusal text
    can never reach the stored lede."""
    from engine.analysis import lede_writer

    called = {"llm": False}

    def _fake_call_llm(*_a, **_k):
        called["llm"] = True
        return _REFUSAL_LEDE, "anthropic/claude-sonnet-4.6"

    monkeypatch.setattr(lede_writer, "_call_llm", _fake_call_llm)
    # Key present so a missing key is NOT what skips the LLM — proving it is
    # the body-thinness guard doing the work.
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    insight = _build_synthetic_insight(
        polarity="positive",
        headline="Tripura announces 11 MW solar capacity addition",
        event_type="event_capacity_addition",
    )
    insight["article"]["content"] = ""  # the failure-mode trigger

    out = lede_writer.write_lede(article_id="empty-body", insight=insight)
    assert called["llm"] is False, "LLM should be skipped for an empty body"
    assert out["model_used"] == "fallback_template"
    assert "article body excerpt is empty" not in out["text"].lower()
    assert "deterministic fallback" not in out["text"].lower()
    # The template still produces a real, headline-grounded lede.
    assert "Tripura" in out["text"]


def test_llm_refusal_text_is_rejected_when_body_present(monkeypatch):
    """Guard (b): even with a long body (so the thinness guard does NOT
    fire), if the LLM still returns refusal text the verification gate
    rejects it and the template fires instead."""
    from engine.analysis import lede_writer

    monkeypatch.setattr(
        lede_writer, "_call_llm",
        lambda *_a, **_k: (_REFUSAL_LEDE, "anthropic/claude-sonnet-4.6"),
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    insight = _build_synthetic_insight(
        polarity="positive",
        headline="Tripura announces 11 MW solar capacity addition",
        event_type="event_capacity_addition",
    )
    # Long, present body — clears MIN_BODY_CHARS so this isolates the
    # refusal-pattern check inside _verify_lede.
    insight["article"]["content"] = (
        "Tripura announced an 11 MW solar capacity addition on 22 June 2026. " * 6
    )

    out = lede_writer.write_lede(article_id="refusal-present-body", insight=insight)
    assert out["model_used"] == "fallback_template"
    assert "deterministic fallback" not in out["text"].lower()
    assert "article body excerpt is empty" not in out["text"].lower()


def test_present_body_lede_is_kept(monkeypatch):
    """Regression guard: a clean LLM lede on a present body is NOT rejected
    by the new guards — they only fire on thin bodies / refusal text."""
    from engine.analysis import lede_writer

    clean = (
        "Adani Power energised 11 MW of solar in Tripura on 22 June 2026. "
        "The addition is modest. The cadence of these prints is the signal."
    )
    monkeypatch.setattr(
        lede_writer, "_call_llm",
        lambda *_a, **_k: (clean, "anthropic/claude-sonnet-4.6"),
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    insight = _build_synthetic_insight(
        polarity="positive",
        headline="Adani Power adds 11 MW Tripura solar",
        event_type="event_capacity_addition",
    )
    insight["article"]["content"] = (
        "Adani Power energised 11 MW of solar capacity in Tripura on 22 June "
        "2026, the company said in a statement. The plant feeds the state grid "
        "and is part of a broader renewable build-out across the north-east."
    )

    out = lede_writer.write_lede(article_id="clean-present-body", insight=insight)
    assert out["model_used"] == "anthropic/claude-sonnet-4.6"
    assert out["text"] == clean


# ---------------------------------------------------------------------------
# Phase 40.B — recommendation topic-drift verifier
# ---------------------------------------------------------------------------


def test_drift_check_drops_off_topic_supplier_engagement_on_cfo_article():
    """The PUMA bug: a CFO appointment article produced 'Launch
    Supplier Engagement Program for Scope 3 Reduction'. The drift
    check must drop this — zero overlap with article title or body."""
    import re
    from engine.analysis.recommendation_engine import _REC_TOPIC_DRIFT_STOPWORDS

    article_title = "PUMA appoints Mark Langer as Chief Financial Officer"
    article_body = (
        "PUMA SE announced the appointment of Mark Langer as Chief "
        "Financial Officer, effective 30 April 2026."
    )
    rec_title = "Launch Supplier Engagement Program for Scope 3 Reduction"

    rec_tokens = [
        t for t in re.findall(r"\b[a-z]{3,}\b", rec_title.lower())
        if t not in _REC_TOPIC_DRIFT_STOPWORDS
    ]
    strong = article_title.lower()
    weak = article_body.lower()
    strong_overlap = [t for t in set(rec_tokens) if t in strong]
    weak_overlap = [t for t in set(rec_tokens) if t in weak]

    # Should fall through both checks → DROP
    assert len(strong_overlap) < 1
    assert len(weak_overlap) < 2


def test_drift_check_keeps_legitimate_rec_with_strong_signal_overlap():
    """A rec that shares even ONE stem with article title (strong
    signal) survives — the verifier is biased toward keeping recs
    that have ANY topical thread to the article. The stem-aware
    match means 'appointment' counts as overlapping with 'appoints'."""
    import re
    from engine.analysis.recommendation_engine import _REC_TOPIC_DRIFT_STOPWORDS

    article_title = "PUMA appoints Mark Langer as Chief Financial Officer"
    rec_title = "Update Investor Communications on CFO Appointment"

    rec_tokens = [
        t for t in re.findall(r"\b[a-z]{3,}\b", rec_title.lower())
        if t not in _REC_TOPIC_DRIFT_STOPWORDS
    ]
    # Stem-aware match (mirrors the verifier's _token_in_universe)
    def _in(token: str, universe: str) -> bool:
        if token in universe:
            return True
        stem = token[:5] if len(token) > 5 else token
        return len(stem) >= 4 and stem in universe
    overlap = [t for t in set(rec_tokens) if _in(t, article_title.lower())]
    # "appointment" stem "appoi" overlaps with "appoints" in title → KEEP
    assert len(overlap) >= 1, f"expected stem-overlap; got tokens={rec_tokens}"


def test_drift_check_keeps_rec_with_multiple_weak_overlaps():
    """When strong signal (title) has no overlap but the article body
    shares 2+ substantive tokens with the rec, keep it."""
    import re
    from engine.analysis.recommendation_engine import _REC_TOPIC_DRIFT_STOPWORDS

    article_title = "Acme Bank Q4 Results"
    article_body = (
        "Acme Bank announced ₹3,200 Cr profit on a 22% lift in retail "
        "lending. Credit growth accelerated across small business and "
        "mortgage portfolios."
    )
    rec_title = "Refine Credit Underwriting in Small Business Lending"

    rec_tokens = [
        t for t in re.findall(r"\b[a-z]{3,}\b", rec_title.lower())
        if t not in _REC_TOPIC_DRIFT_STOPWORDS
    ]
    weak = article_body.lower()
    weak_overlap = [t for t in set(rec_tokens) if t in weak]
    # "credit" + "lending" + "small" + "business" all overlap
    assert len(weak_overlap) >= 2
