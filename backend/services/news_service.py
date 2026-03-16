"""News service — Google News RSS ingestion and scoring.

Per MASTER_BUILD_PLAN Part 1, Layer 1: News Ingestion & Classification
- Google News RSS with domain-driven curation
- Topic tagging, sentiment analysis
"""

import re
from urllib.parse import quote

import feedparser
import structlog

logger = structlog.get_logger()

GOOGLE_NEWS_RSS_BASE = "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"


async def fetch_google_news(query: str, max_results: int = 20) -> list[dict]:
    """Fetch articles from Google News RSS for a given search query."""
    url = GOOGLE_NEWS_RSS_BASE.format(query=quote(query))

    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:max_results]:
            raw_summary = entry.get("summary", "")
            clean_summary = re.sub(r"<[^>]+>", "", raw_summary).replace("&nbsp;", " ").strip() or None

            # Extract image URL from RSS media fields or summary HTML
            image_url = None
            media_content = entry.get("media_content", [])
            if media_content and isinstance(media_content, list):
                image_url = media_content[0].get("url")
            if not image_url:
                media_thumb = entry.get("media_thumbnail", [])
                if media_thumb and isinstance(media_thumb, list):
                    image_url = media_thumb[0].get("url")
            if not image_url:
                # Try extracting from <img> in summary HTML
                img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw_summary)
                if img_match:
                    image_url = img_match.group(1)

            articles.append({
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "source": entry.get("source", {}).get("title", ""),
                "published_at": entry.get("published", ""),
                "summary": clean_summary,
                "image_url": image_url,
            })
        logger.info("news_fetched", query=query, count=len(articles))
        return articles
    except Exception as e:
        logger.error("news_fetch_failed", query=query, error=str(e))
        return []


async def curate_domain_news(
    company_name: str,
    sustainability_query: str | None,
    general_query: str | None,
) -> list[dict]:
    """Fetch domain-driven news per MASTER_BUILD_PLAN Part 3: Domain-Driven App Behavior.

    Combines ESG-specific and general news for the company.
    """
    articles = []

    if sustainability_query:
        articles.extend(await fetch_google_news(sustainability_query, max_results=15))

    if general_query:
        articles.extend(await fetch_google_news(general_query, max_results=10))

    # Fallback: company name + ESG
    if not articles:
        articles.extend(await fetch_google_news(f'"{company_name}" ESG', max_results=10))

    # Deduplicate by URL
    seen_urls = set()
    unique = []
    for a in articles:
        if a["url"] not in seen_urls:
            seen_urls.add(a["url"])
            unique.append(a)

    logger.info("domain_news_curated", company=company_name, total=len(unique))
    return unique
