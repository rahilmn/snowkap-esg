"""Regression tests for the two accuracy fixes from the live-news audit:

1. Event classifier: geopolitical/LNG supply shocks must classify as
   `event_supply_chain_disruption`, not fall through to
   `event_quarterly_results` or `event_default`. Catches the IEEFA /
   OREACO "West Asia's Woes Widen India's Steel Energy Security Chasm"
   article we hit during the Phase 11 live-news audit on 2026-04-24.

2. News fetcher relevance guard: NewsAPI.ai returns articles that contain
   the query phrase anywhere in 2-5 KB of body text — fine for coverage,
   bad for precision. The `_is_article_about_company` guard rejects
   articles that don't mention the company name as a case-insensitive
   phrase in the title or first 800 chars.
"""

from __future__ import annotations

from engine.config import Company


def test_event_classifier_matches_lng_supply_shock() -> None:
    """The IEEFA article (LNG + Strait of Hormuz + West Asia conflict)
    must classify as event_supply_chain_disruption with the correct score
    bounds and ≥5 keyword hits."""
    # Clear caches so the updated TTL keywords load
    from engine.nlp import event_classifier
    event_classifier._cached_rules.cache_clear()
    from engine.ontology import intelligence as intel
    if hasattr(intel.query_event_rules, "cache_clear"):
        intel.query_event_rules.cache_clear()

    title = "West Asia's Woes Widen India's Steel Energy Security Chasm"
    body = (
        "The escalating conflict in West Asia has sent shockwaves through India's "
        "steel industry. Indian steelmakers are scrambling to secure natural gas "
        "and liquefied petroleum gas (LNG). The Strait of Hormuz, a chokepoint "
        "through which nearly 30% of the world's LNG passes, has become a "
        "high-risk zone. Tanker diversions, insurance premium spikes, and delivery "
        "delays have tightened LNG availability across India's west coast. Small "
        "steelmakers have already begun cutting production amid gas shortages."
    )
    ev = event_classifier.classify_event(title, body, theme="Energy")

    assert ev.event_id == "event_supply_chain_disruption", (
        f"Expected event_supply_chain_disruption, got {ev.event_id}. "
        f"Matched keywords: {ev.matched_keywords[:10]}"
    )
    assert ev.score_floor == 5, f"score_floor should be 5, got {ev.score_floor}"
    assert ev.score_ceiling == 9, f"score_ceiling should be 9, got {ev.score_ceiling}"
    assert len(ev.matched_keywords) >= 5, (
        f"Expected ≥5 keyword hits, got {len(ev.matched_keywords)}: "
        f"{ev.matched_keywords}"
    )
    # Must match at least one geopolitical + one LNG-specific term
    lower_hits = " ".join(ev.matched_keywords).lower()
    assert any(k in lower_hits for k in ["strait of hormuz", "west asia", "tanker"])
    assert any(k in lower_hits for k in ["lng", "liquefied natural gas", "gas shortage"])


def test_event_classifier_quarterly_results_still_works() -> None:
    """Regression guard: the supply-chain keyword expansion must NOT
    cannibalise the quarterly-results classifier for an actual earnings
    article."""
    from engine.nlp import event_classifier
    event_classifier._cached_rules.cache_clear()

    title = "JSW Energy Q4 FY26 results beat estimates, EBITDA up 18%"
    body = (
        "JSW Energy reported Q4 FY26 net profit of ₹1,200 crore, beating "
        "analyst estimates. Revenue grew 12% year-on-year. EBITDA margin "
        "expanded to 28%. Management reaffirmed FY27 guidance of 20% growth "
        "in operating profit."
    )
    ev = event_classifier.classify_event(title, body, theme="Corporate Governance")
    assert ev.event_id == "event_quarterly_results", (
        f"Earnings article should stay as quarterly_results, got {ev.event_id}"
    )


def _mk_company(slug: str, name: str) -> Company:
    """Build a minimal Company stub — we only need .name and .slug for the
    relevance guard."""
    return Company(
        name=name,
        slug=slug,
        domain=f"{slug}.example.com",
        industry="Test",
        sasb_category="Utilities",
        market_cap="Large Cap",
        listing_exchange="NSE",
        headquarter_city="Mumbai",
        headquarter_country="IN",
        headquarter_region="South Asia",
        news_queries=[name],
    )


def test_relevance_guard_rejects_sibling_company_article() -> None:
    """Article about JSW Steel (mentions 'JSW Energy' nowhere in title/head)
    must be rejected when targeted at JSW Energy."""
    from engine.ingestion.news_fetcher import _is_article_about_company

    jsw_energy = _mk_company("jsw-energy", "JSW Energy")
    title = "JSW Steel Q4 FY26 results: Profit beats estimates on green steel push"
    body = (
        "JSW Steel reported a 15% increase in Q4 FY26 profit, driven by green "
        "steel output and improved coking coal sourcing. The group's energy "
        "consumption per tonne fell to a record low. JSW Steel continues to "
        "invest in renewable power purchase agreements alongside sister "
        "companies in the JSW Group to decarbonise steel production."
    )
    assert _is_article_about_company(title, body, jsw_energy) is False


def test_relevance_guard_keeps_on_topic_article() -> None:
    """Article that mentions 'JSW Energy' as a phrase in the head must pass."""
    from engine.ingestion.news_fetcher import _is_article_about_company

    jsw_energy = _mk_company("jsw-energy", "JSW Energy")
    title = "JSW Energy commissions first green hydrogen plant in Karnataka"
    body = (
        "JSW Energy, part of the JSW Group, announced today that its Vijayanagar "
        "plant will begin producing green hydrogen at commercial scale."
    )
    assert _is_article_about_company(title, body, jsw_energy) is True


def test_relevance_guard_matches_case_insensitively() -> None:
    from engine.ingestion.news_fetcher import _is_article_about_company

    icici = _mk_company("icici-bank", "ICICI Bank")
    title = "icici bank announces sustainability-linked bond issuance"
    body = "The bank will issue ₹500 Cr in sustainability-linked debt..."
    assert _is_article_about_company(title, body, icici) is True


def test_relevance_guard_rejects_off_topic_article() -> None:
    """Ameriprise Financial article must not slip past when we're looking
    for YES Bank news, even though both are 'financial services' sector."""
    from engine.ingestion.news_fetcher import _is_article_about_company

    yes_bank = _mk_company("yes-bank", "YES Bank")
    title = "Ameriprise Financial Q1 2026 earnings exceed expectations"
    body = (
        "Ameriprise Financial reported a strong performance for the first "
        "quarter of 2026, with earnings per share of $11.26, surpassing the "
        "forecasted $10.24..."
    )
    assert _is_article_about_company(title, body, yes_bank) is False


def test_relevance_guard_handles_newlines_in_title() -> None:
    """Wire-service articles sometimes put the company name across a
    line-wrap. Whitespace normalisation should still catch the phrase."""
    from engine.ingestion.news_fetcher import _is_article_about_company

    jsw_energy = _mk_company("jsw-energy", "JSW Energy")
    title = "JSW\n Energy\t signs PPA with Adani Green"
    body = "Details follow..."
    assert _is_article_about_company(title, body, jsw_energy) is True


def test_relevance_guard_rejects_when_company_mentioned_only_late_in_body() -> None:
    """If the company name only appears at char 2000+, it's a passing
    mention, not an article about the company."""
    from engine.ingestion.news_fetcher import _is_article_about_company

    jsw_energy = _mk_company("jsw-energy", "JSW Energy")
    title = "Indian power sector Q4 review: aggregate trends"
    # 1200 chars of non-company content, then a passing mention
    body = "The Indian power sector saw aggregate tariff revisions. " * 40
    body += " JSW Energy was one of the 15 listed players reviewed."
    assert _is_article_about_company(title, body, jsw_energy) is False
