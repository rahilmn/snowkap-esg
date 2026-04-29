"""Phase 12 hardening — regression tests for the 4 fixes that close the
hallucination vector observed on the Waaree PSPCL solar-auction article
(2026-04-24). That article was a positive contract win but the pipeline
framed it as a ₹807 Cr regulatory crisis because:

  1. One generic keyword ("accountability") picked a specific event type
  2. Wrap-up / digest articles were treated as single-event articles
  3. There was no positive-event ontology (contract wins, certifications)
  4. The verifier caught math drift but not narrative polarity mismatch

Each fix has its own test block below.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# 12.1 — Classifier hardening
# ---------------------------------------------------------------------------


def test_single_generic_keyword_does_not_classify() -> None:
    """A single match on a generic word like 'accountability' must NOT
    trigger a specific event type. Before Phase 12.1, this is exactly how
    the Waaree article got misrouted to event_ngo_report."""
    from engine.nlp.event_classifier import _is_confident_match

    # Single generic keyword: not confident
    assert _is_confident_match(["accountability"]) is False
    assert _is_confident_match(["fine"]) is False
    assert _is_confident_match(["audit"]) is False

    # Two generic keywords: confident (stacking)
    assert _is_confident_match(["fine", "penalty"]) is True

    # One specific multi-word phrase: confident
    assert _is_confident_match(["strait of hormuz"]) is True
    assert _is_confident_match(["consumption-based accountability"]) is True


def test_specific_phrase_detection() -> None:
    from engine.nlp.event_classifier import _is_specific_phrase

    # Multi-word ≥10 chars: specific
    assert _is_specific_phrase("strait of hormuz") is True
    assert _is_specific_phrase("child labour") is True
    assert _is_specific_phrase("audit committee") is True

    # Single word / too short: generic
    assert _is_specific_phrase("accountability") is False  # single word
    assert _is_specific_phrase("fine") is False
    assert _is_specific_phrase("a b c") is False  # too short


def test_waaree_wrapup_article_no_longer_misclassifies_as_ngo_report() -> None:
    """The exact Waaree PSPCL wrap-up article body — before Phase 12.1 this
    classified as event_ngo_report on 'accountability' alone."""
    from engine.nlp import event_classifier
    event_classifier._cached_rules.cache_clear()

    title = "Daily News Wrap-Up: PSPCL Announces Winners of 500 MW Solar Auction"
    body = (
        "Waaree Forever Energies (a subsidiary of Waaree Energies) won the "
        "auction. The Bureau of Energy Efficiency issued operational "
        "guidelines for consumption-based accountability."
    )
    ev = event_classifier.classify_event(title, body, theme="Energy")
    assert ev.event_id != "event_ngo_report", (
        f"Should not classify as event_ngo_report on a single generic "
        f"'accountability' keyword. Got event={ev.event_id} with "
        f"matched_keywords={ev.matched_keywords}"
    )


# ---------------------------------------------------------------------------
# 12.2 — Wrap-up / digest detector
# ---------------------------------------------------------------------------


def _mk_company(slug: str, name: str):
    from engine.config import Company
    return Company(
        name=name, slug=slug, domain=f"{slug}.example.com",
        industry="Test", sasb_category="Utilities", market_cap="Large Cap",
        listing_exchange="NSE", headquarter_city="Mumbai",
        headquarter_country="IN", headquarter_region="South Asia",
        news_queries=[name],
    )


def test_wrapup_detector_catches_daily_news_wrap_up_title() -> None:
    from engine.ingestion.news_fetcher import _is_wrapup_article

    company = _mk_company("waaree-energies", "Waaree Energies")
    # Title marker alone is sufficient
    assert _is_wrapup_article(
        "Daily News Wrap-Up: PSPCL Announces Winners", "body", company
    ) is True
    assert _is_wrapup_article(
        "Morning Digest: India Solar Sector", "body", company
    ) is True
    assert _is_wrapup_article(
        "Weekly Roundup: ESG Stock Movers", "body", company
    ) is True


def test_wrapup_detector_via_org_density_heuristic() -> None:
    """When title isn't obviously a digest but the body mentions many
    distinct orgs and the target company only passingly, flag as wrap-up."""
    from engine.ingestion.news_fetcher import _is_wrapup_article

    waaree = _mk_company("waaree-energies", "Waaree Energies")
    title = "Indian Solar Sector Review"
    # Body has 6+ distinct orgs and only 1 Waaree mention in the first 2KB.
    body = (
        "In today's sector review, SAEL Industries and MB Power Madhya "
        "Pradesh announced their Q4 results. Adani Green Energy and Tata "
        "Power Solar continued to lead market share. ReNew Power and Azure "
        "Power reported strong capacity additions. Waaree Energies also "
        "had a quarterly update. Hindustan Power and Technique Solaire "
        "expanded their capacity. JSW Energy and Jindal Steel also "
        "participated in the market. The NTPC Limited tender closed last "
        "week with bids from BHEL and Larsen Toubro. Coal India announced "
        "new capacity plans, while NLC India Limited continued its solar "
        "push. Indian Oil Corporation and GAIL India completed new gas "
        "pipelines. Reliance Industries and Hindustan Petroleum announced "
        "new refinery upgrades. Bharat Petroleum Corporation and Oil India "
        "Limited signed new supply contracts."
    )
    assert _is_wrapup_article(title, body, waaree) is True


def test_wrapup_detector_keeps_real_company_article() -> None:
    """A deep-dive article mentioning many peers but with heavy company
    coverage must NOT be flagged."""
    from engine.ingestion.news_fetcher import _is_wrapup_article

    waaree = _mk_company("waaree-energies", "Waaree Energies")
    title = "Waaree Energies Q4 FY26 results beat estimates"
    body = (
        "Waaree Energies reported strong Q4. Waaree's revenue grew 18%. "
        "Waaree's CEO said solar cell exports remain robust. Competitors "
        "Adani Green and ReNew Power also reported growth but Waaree "
        "maintained its lead in cell manufacturing. Waaree's capex pipeline "
        "expands. Waaree's order book at 18 GW." * 4
    )
    assert _is_wrapup_article(title, body, waaree) is False


# ---------------------------------------------------------------------------
# 12.3 — Positive-event ontology
# ---------------------------------------------------------------------------


def test_waaree_auction_classifies_as_contract_win() -> None:
    """End-to-end: the PSPCL article should now classify as
    event_contract_win, not misrouted to ngo_report or transition_announcement."""
    from engine.nlp import event_classifier
    event_classifier._cached_rules.cache_clear()

    title = "PSPCL Announces Winners of 500 MW Solar Auction"
    body = (
        "Waaree Forever Energies, a subsidiary of Waaree Energies, won the "
        "auction to procure 500 MW of solar power. The winners emerged at a "
        "lowest tariff bid of ₹2.85/kWh under PSPCL's solar auction. The "
        "auction announces winners committed to long-term procurement of "
        "renewable energy."
    )
    ev = event_classifier.classify_event(title, body, theme="Energy")
    assert ev.event_id == "event_contract_win", (
        f"Expected event_contract_win, got {ev.event_id}. "
        f"matched_keywords={ev.matched_keywords}"
    )
    assert ev.score_floor == 3
    assert ev.score_ceiling == 7


def test_capacity_addition_classifies() -> None:
    from engine.nlp import event_classifier
    event_classifier._cached_rules.cache_clear()

    title = "JSW Energy commissions 500 MW solar plant in Karnataka"
    body = (
        "JSW Energy commissioned its Vijayanagar solar plant this week. The "
        "commercial operation date was achieved ahead of schedule. The plant "
        "is grid-connected and will begin production start next month."
    )
    ev = event_classifier.classify_event(title, body, theme="Energy")
    assert ev.event_id == "event_capacity_addition", (
        f"Expected event_capacity_addition, got {ev.event_id}. "
        f"matched_keywords={ev.matched_keywords}"
    )


def test_esg_certification_classifies() -> None:
    from engine.nlp import event_classifier
    event_classifier._cached_rules.cache_clear()

    title = "Adani Power receives ISO 14001 certification and MSCI ESG upgrade"
    body = (
        "Adani Power announced it has received ISO 14001 certification for "
        "environmental management. The MSCI ESG upgrade from B to BB came "
        "alongside DJSI inclusion in the emerging markets index."
    )
    ev = event_classifier.classify_event(title, body, theme="Corporate Governance")
    assert ev.event_id == "event_esg_certification", (
        f"Expected event_esg_certification, got {ev.event_id}. "
        f"matched_keywords={ev.matched_keywords}"
    )


# ---------------------------------------------------------------------------
# 12.4 — Narrative-data coherence check
# ---------------------------------------------------------------------------


def test_coherence_check_downgrades_positive_event_framed_negatively() -> None:
    """The exact Waaree hallucination pattern: event_contract_win (positive),
    but the LLM output frames it as CRITICAL with crisis language. The
    coherence check should downgrade materiality to MODERATE."""
    from engine.analysis.output_verifier import verify_narrative_coherence

    # Insight the LLM produced BEFORE coherence check (crisis framing on
    # a positive event — exactly the pre-Phase-12 Waaree output shape)
    insight = {
        "headline": "Waaree Energies faces ₹807 Cr regulatory exposure",
        "decision_summary": {
            "materiality": "CRITICAL",
            "key_risk": "₹477.5 Cr contingent liability from regulatory failure with precedent for SEBI trading restrictions and potential ESG rating downgrade",
            "top_opportunity": "",
        },
    }
    adjusted, report = verify_narrative_coherence(
        insight, event_id="event_contract_win", nlp_sentiment=1
    )

    # Materiality should be downgraded because event is POSITIVE (+1) but
    # insight_polarity is NEGATIVE (-1)
    assert adjusted["decision_summary"]["materiality"] == "HIGH", (
        f"Expected CRITICAL → HIGH downgrade, got "
        f"{adjusted['decision_summary']['materiality']}"
    )
    # Warning must surface the mismatch
    assert any("coherence mismatch" in c for c in report.corrections), (
        f"Expected coherence mismatch warning, got {report.corrections}"
    )


def test_coherence_check_passes_when_polarities_align() -> None:
    """Negative event + negative insight (e.g. SEBI penalty → CRITICAL) must
    NOT trigger a downgrade."""
    from engine.analysis.output_verifier import verify_narrative_coherence

    insight = {
        "decision_summary": {
            "materiality": "CRITICAL",
            "key_risk": "₹50 Cr SEBI penalty with regulatory escalation risk",
            "top_opportunity": "",
        },
    }
    adjusted, report = verify_narrative_coherence(
        insight, event_id="event_social_violation", nlp_sentiment=-2
    )
    # No change — polarities aligned (event=-1, insight=-1)
    assert adjusted["decision_summary"]["materiality"] == "CRITICAL"
    assert not any("coherence mismatch" in c for c in report.corrections)


def test_coherence_check_noop_when_event_id_unknown() -> None:
    """Event type we don't classify as positive or negative → no coherence
    judgement possible → leave materiality alone."""
    from engine.analysis.output_verifier import verify_narrative_coherence

    insight = {
        "decision_summary": {
            "materiality": "CRITICAL",
            "key_risk": "some risk text longer than 20 chars for polarity",
            "top_opportunity": "",
        },
    }
    adjusted, report = verify_narrative_coherence(
        insight, event_id="event_quarterly_results", nlp_sentiment=None
    )
    # Neutral event — no change
    assert adjusted["decision_summary"]["materiality"] == "CRITICAL"
