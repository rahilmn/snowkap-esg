"""News service — Multi-source news ingestion (Google News RSS + NewsAPI).

Per MASTER_BUILD_PLAN Part 1, Layer 1: News Ingestion & Classification
- Google News RSS with domain-driven curation
- NewsAPI.org for Bloomberg, Reuters, and 150+ sources with images
- Deduplication by URL across all sources
"""

import re
from urllib.parse import quote

import feedparser
import httpx
import structlog

from backend.core.config import settings

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
        logger.info("google_news_fetched", query=query, count=len(articles))
        return articles
    except Exception as e:
        logger.error("google_news_fetch_failed", query=query, error=str(e))
        return []


async def fetch_newsapi(query: str, max_results: int = 20) -> list[dict]:
    """Fetch articles from NewsAPI.org — Bloomberg, Reuters, 150+ sources with images.

    Phase 2: Secondary news source with better freshness and always-included images.
    Free tier: 100 requests/day, 100 articles/request.
    """
    if not settings.NEWSAPI_KEY:
        return []

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": min(max_results, 100),
                    "apiKey": settings.NEWSAPI_KEY,
                },
            )
            data = resp.json()

        if data.get("status") != "ok":
            logger.warning("newsapi_error", message=data.get("message", "unknown"))
            return []

        articles = []
        for a in data.get("articles", []):
            title = a.get("title", "")
            # Skip removed articles
            if not title or title == "[Removed]":
                continue
            articles.append({
                "title": title,
                "url": a.get("url", ""),
                "source": a.get("source", {}).get("name", ""),
                "published_at": a.get("publishedAt", ""),
                "summary": a.get("description"),
                "image_url": a.get("urlToImage"),  # NewsAPI always includes images
            })

        logger.info("newsapi_fetched", query=query, count=len(articles))
        return articles
    except Exception as e:
        logger.error("newsapi_fetch_failed", query=query, error=str(e))
        return []


async def curate_domain_news(
    company_name: str,
    sustainability_query: str | None,
    general_query: str | None,
) -> list[dict]:
    """Fetch domain-driven news from BOTH Google News RSS and NewsAPI.

    Combines ESG-specific and general news, deduplicates by URL.
    """
    articles = []

    # Source 1: Google News RSS
    if sustainability_query:
        articles.extend(await fetch_google_news(sustainability_query, max_results=15))
    if general_query:
        articles.extend(await fetch_google_news(general_query, max_results=10))

    # Source 2: NewsAPI (Bloomberg, Reuters, etc.)
    newsapi_query = f"{company_name} ESG sustainability"
    newsapi_articles = await fetch_newsapi(newsapi_query, max_results=15)
    articles.extend(newsapi_articles)

    # Source 3: Competitor news (if competitors available)
    try:
        from sqlalchemy import select as sa_select
        from backend.core.database import async_session_factory
        from backend.models.company import Company

        async with async_session_factory() as db:
            # Find company by name (fuzzy) or by matching domain
            comp_result = await db.execute(
                sa_select(Company).where(
                    sa_select(Company).where(Company.name.ilike(f"%{company_name.split()[0]}%")).exists()
                    if " " in company_name
                    else Company.name == company_name
                ).limit(1)
            )
            company = comp_result.scalars().first()

            # Fallback: search by any company for this name prefix
            if not company:
                comp_result = await db.execute(
                    sa_select(Company).where(Company.name.ilike(f"%{company_name.split()[0]}%")).limit(1)
                )
                company = comp_result.scalars().first()

            if company and company.competitors:
                for comp in company.competitors[:3]:
                    if isinstance(comp, dict) and comp.get("name"):
                        comp_articles = await fetch_google_news(
                            f'"{comp["name"]}" ESG sustainability', max_results=5
                        )
                        for ca in comp_articles:
                            ca["is_competitor_news"] = True
                            ca["competitor_name"] = comp["name"]
                        articles.extend(comp_articles)
                logger.info("competitor_news_fetched", competitors=len(company.competitors[:3]))
    except Exception as e:
        logger.debug("competitor_news_fetch_skipped", error=str(e))

    # Fallback: company name + ESG (Google News)
    if not articles:
        articles.extend(await fetch_google_news(f'"{company_name}" ESG', max_results=10))

    # Filter out articles older than 2 months and deduplicate by URL
    from datetime import datetime, timedelta, timezone
    from email.utils import parsedate_to_datetime

    two_months_ago = datetime.now(timezone.utc) - timedelta(days=60)
    seen_urls: set[str] = set()
    unique = []
    for a in articles:
        url = a.get("url", "")
        if not url or url in seen_urls:
            continue
        # Skip old articles
        pub = a.get("published_at")
        if pub:
            try:
                pub_dt = parsedate_to_datetime(pub)
                if pub_dt < two_months_ago:
                    continue
            except Exception as exc:
                logger.debug("rss_date_parse_failed", published=pub, error=str(exc))
        seen_urls.add(url)
        unique.append(a)

    logger.info(
        "domain_news_curated",
        company=company_name,
        total=len(unique),
        google_news=len(articles) - len(newsapi_articles),
        newsapi=len(newsapi_articles),
    )
    return unique
