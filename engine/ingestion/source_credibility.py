"""Phase 25 W8a — source credibility whitelist + tier boost.

The user wants Mint, ET, Bloomberg, Reuters, and ESG-specialist
publishers to BEAT generic aggregator junk when both compete for one
of the 3 top-tier slots a customer's overnight batch receives.

Approach: a domain whitelist that returns +1 to ``source_credibility_tier``
when the article URL matches. The W7 selector + Stage 1 NLP extractor
both consume this. Whitelisted domains rank higher in the selector
AND get a higher source_credibility_tier in the LLM prompt context,
which the Stage 10 deep-insight prompt cites in framing rationales.

Conservative: this is a curated list of 30 domains, NOT a regex/heuristic.
Easier to audit, easier to extend, no false positives. Add new entries
to ``WHITELIST_DOMAINS`` and the change ships in the next deploy.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Whitelisted domains — credibility boost applies when article URL matches
# ---------------------------------------------------------------------------

# Three buckets:
#   * Tier-1 financial press (boost +2): Bloomberg, Reuters, FT, WSJ
#   * Tier-2 trusted Indian press (boost +1): Mint, ET, Business Standard
#   * Tier-2 ESG-specialist publishers (boost +1)
#   * Tier-2 regulator + framework primary sources (boost +1)
TIER_1_DOMAINS: frozenset[str] = frozenset({
    "bloomberg.com",
    "reuters.com",
    "ft.com",          # Financial Times
    "wsj.com",         # Wall Street Journal
})

TIER_2_INDIAN_PRESS: frozenset[str] = frozenset({
    "livemint.com",
    "economictimes.indiatimes.com",
    "business-standard.com",
    "businessinsider.in",
    "moneycontrol.com",
    "financialexpress.com",
    "thehindubusinessline.com",
    "ndtvprofit.com",
    "indianexpress.com",  # Indian Express business desk
})

TIER_2_ESG_SPECIALIST: frozenset[str] = frozenset({
    "esgtoday.com",
    "edie.net",
    "greenbiz.com",
    "sustainability-news.net",
    "environmental-finance.com",
    "esginvestor.net",
    "climate-policy-initiative.org",
    "wri.org",                      # World Resources Institute
    "iisd.org",                     # International Institute for Sustainable Development
    "responsible-investor.com",
})

TIER_2_REGULATORS: frozenset[str] = frozenset({
    "sebi.gov.in",
    "rbi.org.in",
    "esg.sec.gov",
    "esma.europa.eu",
    "fca.org.uk",
    "bis.gov.in",                   # Bureau of Indian Standards
    "cpcb.nic.in",                  # CPCB (pollution control)
    "ec.europa.eu",                 # CSRD / Taxonomy
})

# Combined lookup set used by score()
ALL_WHITELISTED: frozenset[str] = (
    TIER_1_DOMAINS | TIER_2_INDIAN_PRESS | TIER_2_ESG_SPECIALIST | TIER_2_REGULATORS
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score(url: str) -> int:
    """Return the credibility-tier boost for a URL.

    Returns:
      * 0 — domain not in whitelist (default credibility)
      * +1 — Tier-2 source (Indian press, ESG specialist, regulator)
      * +2 — Tier-1 source (Bloomberg, Reuters, FT, WSJ)

    The W7 article_selector adds this to the article's base
    ``source_credibility_tier`` (default 3) before computing the rank
    score. Stage 1 NLP extractor uses the boosted tier in the LLM
    context block.
    """
    domain = _extract_domain(url)
    if not domain:
        return 0
    if domain in TIER_1_DOMAINS:
        return 2
    if domain in ALL_WHITELISTED:
        return 1
    # Subdomain handling — "news.bloomberg.com" should match "bloomberg.com"
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in TIER_1_DOMAINS:
            return 2
        if candidate in ALL_WHITELISTED:
            return 1
    return 0


def is_whitelisted(url: str) -> bool:
    """True if the URL's domain (or any of its parent domains) is on the
    whitelist. Useful as a feed pre-filter or alert highlight."""
    return score(url) > 0


def tier_label(boost: int) -> str:
    """Human-readable label for a credibility boost.
    Returned by the API + included in audit log entries."""
    if boost >= 2:
        return "tier-1 (Bloomberg/Reuters/FT/WSJ)"
    if boost == 1:
        return "tier-2 (Indian press / ESG specialist / regulator)"
    return "tier-3 default (aggregator or unknown)"


def list_whitelisted_domains() -> dict[str, list[str]]:
    """Return the whitelist grouped by tier — used by the API
    endpoint that surfaces the whitelist in the admin UI."""
    return {
        "tier_1_financial_press": sorted(TIER_1_DOMAINS),
        "tier_2_indian_press": sorted(TIER_2_INDIAN_PRESS),
        "tier_2_esg_specialist": sorted(TIER_2_ESG_SPECIALIST),
        "tier_2_regulators": sorted(TIER_2_REGULATORS),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_domain(url: str) -> str:
    """Return the lowercase netloc, stripped of leading 'www.'."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
    except (ValueError, AttributeError):
        return ""
    netloc = (parsed.netloc or parsed.path or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    # Strip port if present
    if ":" in netloc:
        netloc = netloc.split(":")[0]
    return netloc.strip()
