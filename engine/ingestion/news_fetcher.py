"""Fetch ESG news for target companies.

Two sources:
1. Google News RSS via feedparser (no API key needed)
2. NewsAPI.org (optional, requires ``NEWSAPI_KEY``)

Outputs normalized JSON files to ``data/inputs/news/{company_slug}/``
with deduplication tracked in ``data/processed/article_hashes.json``.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import feedparser
import requests

# Allow `python -m engine.ingestion.news_fetcher` without PYTHONPATH.
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.config import Company, get_company, get_data_path, get_newsapi_key, load_companies, load_settings

logger = logging.getLogger(__name__)

GOOGLE_NEWS_URL = (
    "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
)
NEWSAPI_URL = "https://newsapi.org/v2/everything"
HTML_TAG = re.compile(r"<[^>]+>")
WHITESPACE = re.compile(r"\s+")


@dataclass
class IngestedArticle:
    id: str
    title: str
    content: str
    summary: str
    source: str
    url: str
    published_at: str
    company_slug: str
    source_type: str  # google_news | newsapi | file | prompt
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_html(text: str) -> str:
    if not text:
        return ""
    clean = HTML_TAG.sub(" ", text)
    clean = html.unescape(clean)
    return WHITESPACE.sub(" ", clean).strip()


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _load_processed() -> set[str]:
    path = get_data_path("processed", "article_hashes.json")
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("hashes", []))
    except (json.JSONDecodeError, OSError):
        logger.warning("processed hash file corrupt, rebuilding")
        return set()


def _save_processed(hashes: set[str]) -> None:
    path = get_data_path("processed", "article_hashes.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "hashes": sorted(hashes)}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _parse_published(raw: str | None) -> str:
    if not raw:
        return datetime.now(timezone.utc).isoformat()
    try:
        # Try RSS date format (e.g., 'Sat, 07 Apr 2026 10:00:00 GMT')
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return datetime.now(timezone.utc).isoformat()


def _write_article(article: IngestedArticle) -> Path:
    date_prefix = article.published_at[:10]  # YYYY-MM-DD
    folder = get_data_path("inputs", "news", article.company_slug)
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{date_prefix}_{article.id}.json"
    path = folder / filename
    path.write_text(json.dumps(asdict(article), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Source 1: Google News RSS
# ---------------------------------------------------------------------------


def fetch_google_news(query: str, max_results: int = 20) -> list[dict]:
    """Fetch Google News RSS for a search query."""
    feed_url = GOOGLE_NEWS_URL.format(query=quote(query))
    logger.debug("Fetching Google News RSS: %s", feed_url)
    try:
        parsed = feedparser.parse(feed_url)
    except Exception as exc:  # noqa: BLE001 — defensive, network calls
        logger.error("feedparser failed for '%s': %s", query, exc)
        return []

    entries = parsed.entries[:max_results] if parsed.entries else []
    articles: list[dict] = []
    for entry in entries:
        url = entry.get("link") or ""
        if not url:
            continue
        title = _strip_html(entry.get("title") or "")
        summary = _strip_html(entry.get("summary") or "")
        source = ""
        if entry.get("source"):
            try:
                source = entry.source.get("title", "")
            except AttributeError:
                source = str(entry.source)
        articles.append(
            {
                "title": title,
                "summary": summary,
                "content": summary,  # RSS-only — no full content yet
                "source": source or "Google News",
                "url": url,
                "published_at": _parse_published(entry.get("published")),
            }
        )
    return articles


# ---------------------------------------------------------------------------
# Source 2: NewsAPI.org (optional)
# ---------------------------------------------------------------------------


def fetch_newsapi(query: str, max_results: int = 20) -> list[dict]:
    """Fetch from NewsAPI.org if NEWSAPI_KEY is set."""
    api_key = get_newsapi_key()
    if not api_key:
        return []
    try:
        resp = requests.get(
            NEWSAPI_URL,
            params={
                "q": query,
                "pageSize": max_results,
                "language": "en",
                "sortBy": "publishedAt",
                "apiKey": api_key,
            },
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("NewsAPI fetch failed for '%s': %s", query, exc)
        return []

    payload = resp.json()
    articles: list[dict] = []
    for item in payload.get("articles", []):
        url = item.get("url") or ""
        if not url:
            continue
        articles.append(
            {
                "title": _strip_html(item.get("title") or ""),
                "summary": _strip_html(item.get("description") or ""),
                "content": _strip_html(item.get("content") or item.get("description") or ""),
                "source": (item.get("source") or {}).get("name") or "NewsAPI",
                "url": url,
                "published_at": _parse_published(item.get("publishedAt")),
            }
        )
    return articles


# ---------------------------------------------------------------------------
# Source 3: NewsAPI.ai (Event Registry) — full article text
# ---------------------------------------------------------------------------

NEWSAPI_AI_URL = "https://eventregistry.org/api/v1/article/getArticles"


def fetch_newsapi_ai(query: str, max_results: int = 5) -> list[dict]:
    """Fetch from NewsAPI.ai (Event Registry) with full article body.

    Returns articles with 2,000-5,000+ chars of content — dramatically
    better than Google News RSS (87 chars) or NewsAPI.org (200 chars).
    """
    import os

    api_key = os.environ.get("NEWSAPI_AI_KEY", "")
    if not api_key:
        return []

    try:
        resp = requests.post(
            NEWSAPI_AI_URL,
            json={
                "action": "getArticles",
                "keyword": query,
                "articlesPage": 1,
                "articlesCount": min(max_results, 10),  # conserve free tier tokens
                "articlesSortBy": "date",
                "includeArticleBody": True,
                "articleBodyLen": -1,  # full body
                "resultType": "articles",
                "lang": "eng",
                "apiKey": api_key,
            },
            timeout=20,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("NewsAPI.ai fetch failed for '%s': %s", query, exc)
        return []

    payload = resp.json()
    articles: list[dict] = []
    for item in payload.get("articles", {}).get("results", []):
        url = item.get("url") or ""
        if not url:
            continue
        body = item.get("body") or ""
        title = item.get("title") or ""
        source_name = (item.get("source") or {}).get("title") or "NewsAPI.ai"
        published = item.get("dateTime") or item.get("date") or ""

        articles.append(
            {
                "title": _strip_html(title),
                "summary": _strip_html(body[:500]) if body else title,
                "content": _strip_html(body),  # FULL ARTICLE TEXT
                "source": source_name,
                "url": url,
                "published_at": _parse_published(published),
                "metadata": {
                    "sentiment": item.get("sentiment"),
                    "source_type": "newsapi_ai",
                    "concepts": [
                        c.get("label", {}).get("eng", "")
                        for c in (item.get("concepts") or [])[:5]
                    ],
                },
            }
        )
    logger.info("NewsAPI.ai: %d articles for '%s' (avg %d chars)",
                len(articles), query,
                sum(len(a["content"]) for a in articles) // max(len(articles), 1))
    return articles


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def fetch_for_company(
    company: Company,
    max_per_query: int | None = None,
    persist: bool = True,
) -> list[IngestedArticle]:
    """Fetch news for one company across all configured queries."""
    settings = load_settings()
    limit = max_per_query or settings.get("ingestion", {}).get(
        "max_articles_per_company_per_run", 20
    )
    processed = _load_processed()

    raw_articles: list[dict] = []
    seen_urls: set[str] = set()
    for query in company.news_queries:
        # Prioritize NewsAPI.ai (full text) → NewsAPI.org → Google News RSS (headline only)
        for source_type, fetcher in (
            ("newsapi_ai", fetch_newsapi_ai),
            ("newsapi", fetch_newsapi),
            ("google_news", fetch_google_news),
        ):
            for art in fetcher(query, max_results=limit):
                if art["url"] in seen_urls:
                    continue
                seen_urls.add(art["url"])
                art.setdefault("source_type", source_type)
                art["query"] = query
                raw_articles.append(art)

    fresh: list[IngestedArticle] = []
    for raw in raw_articles:
        h = _url_hash(raw["url"])
        if h in processed:
            continue
        processed.add(h)
        article = IngestedArticle(
            id=h,
            title=raw["title"],
            content=raw["content"],
            summary=raw["summary"],
            source=raw["source"],
            url=raw["url"],
            published_at=raw["published_at"],
            company_slug=company.slug,
            source_type=raw.get("source_type", "google_news"),
            metadata={"query": raw.get("query", "")},
        )
        fresh.append(article)
        if persist:
            _write_article(article)

    if persist and fresh:
        _save_processed(processed)
    logger.info(
        "news_fetcher: %s -> fetched %s, new %s",
        company.slug,
        len(raw_articles),
        len(fresh),
    )
    return fresh


def fetch_all_companies() -> dict[str, int]:
    """Run fetch_for_company for every target company."""
    summary: dict[str, int] = {}
    for company in load_companies():
        fresh = fetch_for_company(company)
        summary[company.slug] = len(fresh)
    return summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Fetch ESG news for target companies")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--company", help="Company slug (e.g. adani-power)")
    group.add_argument("--all", action="store_true", help="Fetch for all 7 companies")
    parser.add_argument("--max", type=int, default=None, help="Max articles per query")
    args = parser.parse_args(argv)

    if args.all:
        summary = fetch_all_companies()
    else:
        company = get_company(args.company)
        fresh = fetch_for_company(company, max_per_query=args.max)
        summary = {company.slug: len(fresh)}

    print("\nIngested articles per company:")
    for slug, count in summary.items():
        print(f"  {slug}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
