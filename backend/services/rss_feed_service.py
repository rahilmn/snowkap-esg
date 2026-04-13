"""Direct RSS Feed Service — Publication-level polling for guaranteed coverage.

Phase A1: Bypasses Google News ranking by polling publication RSS feeds directly.
Covers: Mint, Economic Times, Business Standard, Moneycontrol, ESG Today, Reuters.

Returns dicts in the same format as fetch_google_news() for seamless pipeline integration.
"""

import re
from datetime import datetime, timedelta, timezone

import feedparser
import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Feed registry — priority-ordered list of publication RSS feeds
# Each entry: (source_name, feed_url, esg_keywords_required)
# esg_keywords_required=True  → only include articles that contain ESG keywords
# esg_keywords_required=False → include all articles (already ESG-filtered feeds)
# ---------------------------------------------------------------------------

PUBLICATION_FEEDS: list[tuple[str, str, bool]] = [
    # Indian financial press — critical coverage
    ("Mint", "https://www.livemint.com/rss/money", True),
    ("Mint", "https://www.livemint.com/rss/companies", True),
    ("Economic Times", "https://economictimes.indiatimes.com/rssfeedstopstories.cms", True),
    ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", True),
    ("Business Standard", "https://www.business-standard.com/rss/home_page_top_stories.rss", True),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/latestnews.xml", True),
    ("Financial Express", "https://www.financialexpress.com/feed/", True),
    ("Business Today", "https://www.businesstoday.in/rssfeeds/", True),
    # ESG-specific feeds — no keyword filter needed
    ("ESG Today", "https://www.esgtoday.com/feed/", False),
    # Global sustainability
    ("Reuters", "https://feeds.reuters.com/reuters/businessNews", True),
]

# ESG keyword filter — article must contain at least one of these to pass
ESG_KEYWORDS: set[str] = {
    "esg", "sustainability", "sustainable", "environment", "climate", "carbon",
    "emissions", "renewable", "green", "governance", "social impact",
    "brsr", "sebi", "disclosure", "net zero", "scope 1", "scope 2", "scope 3",
    "gri", "tcfd", "csrd", "biodiversity", "water", "waste", "compliance",
    "workforce", "diversity", "inclusion", "supply chain", "ethics", "corruption",
    "financed emissions", "green bond", "sustainable finance", "climate risk",
    "deforestation", "human rights", "labour", "labor", "community", "stakeholder",
    "regulation", "penalty", "fine", "violation", "audit", "rating", "index",
    "esg score", "transition", "stranded asset", "taxonomy", "responsible",
}


def _passes_esg_filter(title: str, summary: str) -> bool:
    """Return True if the article mentions at least one ESG keyword."""
    text = (title + " " + (summary or "")).lower()
    return any(kw in text for kw in ESG_KEYWORDS)


def _parse_feed_entry(entry: dict, source_name: str) -> dict:
    """Normalize a feedparser entry into the standard article dict."""
    raw_summary = entry.get("summary", "")
    clean_summary = re.sub(r"<[^>]+>", "", raw_summary).replace("&nbsp;", " ").strip() or None

    image_url = None
    for field in ("media_content", "media_thumbnail"):
        items = entry.get(field, [])
        if items and isinstance(items, list):
            image_url = items[0].get("url")
            if image_url:
                break
    if not image_url:
        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw_summary)
        if img_match:
            image_url = img_match.group(1)

    return {
        "title": entry.get("title", "").strip(),
        "url": entry.get("link", "").strip(),
        "source": source_name,
        "published_at": entry.get("published", ""),
        "summary": clean_summary,
        "image_url": image_url,
    }


async def fetch_publication_feeds(
    max_age_hours: int = 48,
    max_per_feed: int = 20,
) -> list[dict]:
    """Poll all registered publication RSS feeds and return ESG-filtered articles.

    Args:
        max_age_hours: Skip articles older than this many hours (default 48h).
        max_per_feed: Max articles to take from each feed (default 20).

    Returns:
        Deduplicated list of article dicts (same schema as fetch_google_news).
    """
    from backend.services.news_service import ONE_PER_DOMAIN

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    seen_urls: set[str] = set()
    seen_capped_domains: set[str] = set()
    results: list[dict] = []

    for source_name, feed_url, requires_esg_filter in PUBLICATION_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            count = 0
            for entry in feed.entries:
                if count >= max_per_feed:
                    break

                url = entry.get("link", "").strip()
                if not url or url in seen_urls:
                    continue
                # Cap at one article per social/blog domain
                capped_domain = next((d for d in ONE_PER_DOMAIN if d in url), None)
                if capped_domain:
                    if capped_domain in seen_capped_domains:
                        continue
                    seen_capped_domains.add(capped_domain)

                article = _parse_feed_entry(entry, source_name)

                # Age filter
                pub = article.get("published_at")
                if pub:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub_dt = parsedate_to_datetime(pub)
                        if pub_dt.tzinfo is None:
                            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                        if pub_dt < cutoff:
                            continue
                    except Exception:
                        pass

                # ESG keyword filter
                if requires_esg_filter and not _passes_esg_filter(
                    article["title"], article.get("summary") or ""
                ):
                    continue

                seen_urls.add(url)
                results.append(article)
                count += 1

            logger.info("rss_feed_polled", source=source_name, feed=feed_url, added=count)

        except Exception as exc:
            logger.warning("rss_feed_failed", source=source_name, feed=feed_url, error=str(exc))

    logger.info("publication_feeds_complete", total_articles=len(results))
    return results


# Industry-specific keywords — articles must match the company's sector to be relevant
INDUSTRY_KEYWORDS: dict[str, set[str]] = {
    "financials": {
        "bank", "banking", "rbi", "npa", "lending", "credit", "nbfc", "fintech",
        "loan", "deposit", "interest rate", "monetary policy", "insurance", "mutual fund",
        "amc", "asset management", "sebi", "stock exchange", "capital market", "ipo",
        "microfinance", "upi", "payment", "fraud", "default", "restructuring",
    },
    "infrastructure": {
        "power", "energy", "electricity", "coal", "thermal", "grid", "transmission",
        "generation", "distribution", "tariff", "discom", "megawatt", "plant",
        "infrastructure", "construction", "highway", "port", "logistics",
        "capacity addition", "power purchase", "cerc", "serc",
    },
    "renewable": {
        "solar", "wind", "renewable", "green energy", "module", "panel", "inverter",
        "photovoltaic", "turbine", "clean energy", "hydrogen", "battery", "storage",
        "ev", "electric vehicle", "charging", "sustainability transition",
        "green hydrogen", "electrolyser", "rooftop", "utility scale",
    },
}


def _get_industry_keywords(industry: str | None) -> set[str]:
    """Get industry-specific keywords for filtering. Returns empty set if no match."""
    if not industry:
        return set()
    ind_lower = industry.lower()
    for key, keywords in INDUSTRY_KEYWORDS.items():
        if key in ind_lower or ind_lower in key:
            return keywords
    return set()


def _is_industry_relevant(title: str, summary: str, industry_keywords: set[str]) -> bool:
    """Return True if the article matches the company's industry keywords."""
    if not industry_keywords:
        return False
    text = (title + " " + (summary or "")).lower()
    # Require at least one industry keyword AND one ESG keyword
    has_industry = any(kw in text for kw in industry_keywords)
    has_esg = any(kw in text for kw in ESG_KEYWORDS)
    return has_industry and has_esg


async def fetch_publication_feeds_for_company(
    company_name: str,
    max_age_hours: int = 48,
    max_per_feed: int = 20,
    industry: str | None = None,
) -> list[dict]:
    """Fetch publication feeds and filter for company + industry relevance.

    Articles pass if they either:
    - Mention the company name (case-insensitive), OR
    - Match the company's INDUSTRY keywords AND contain ESG keywords

    Generic ESG articles (not industry-specific) are excluded to prevent
    the same news appearing across all companies.
    """
    all_articles = await fetch_publication_feeds(
        max_age_hours=max_age_hours,
        max_per_feed=max_per_feed,
    )
    company_lower = company_name.lower()
    ind_keywords = _get_industry_keywords(industry)

    # Also match competitor names if they appear in company_name variants
    # e.g., "ICICI" matches "ICICI Bank", "ICICI Prudential", etc.
    company_short = company_lower.split()[0] if company_lower else ""

    return [
        a for a in all_articles
        if company_lower in (a["title"] + " " + (a.get("summary") or "")).lower()
        or (company_short and len(company_short) > 3 and company_short in (a["title"] + " " + (a.get("summary") or "")).lower())
        or _is_industry_relevant(a["title"], a.get("summary") or "", ind_keywords)
    ]
