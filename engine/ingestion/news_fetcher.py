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
from engine.ingestion.dedup import SemanticDedup, is_fresh

logger = logging.getLogger(__name__)

GOOGLE_NEWS_URL = (
    "https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"
)
NEWSAPI_URL = "https://newsapi.org/v2/everything"

# Phase 23A — Google News locale per HQ country. Default to English-US so a
# newly-onboarded German or American company doesn't get India-filtered news.
# Add new countries as they come up — keys are exact `headquarter_country`
# strings from `config/companies.json`.
_GOOGLE_NEWS_LOCALES: dict[str, tuple[str, str, str]] = {
    "India": ("en-IN", "IN", "IN:en"),
    "United States": ("en-US", "US", "US:en"),
    "United Kingdom": ("en-GB", "GB", "GB:en"),
    "Germany": ("de", "DE", "DE:de"),
    "France": ("fr", "FR", "FR:fr"),
    "Netherlands": ("nl", "NL", "NL:nl"),
    "Italy": ("it", "IT", "IT:it"),
    "Spain": ("es", "ES", "ES:es"),
    "Sweden": ("sv", "SE", "SE:sv"),
    "Singapore": ("en-SG", "SG", "SG:en"),
    "Australia": ("en-AU", "AU", "AU:en"),
    "Canada": ("en-CA", "CA", "CA:en"),
    "Japan": ("ja", "JP", "JP:ja"),
    "China": ("zh-CN", "CN", "CN:zh-Hans"),
}
_GOOGLE_NEWS_DEFAULT_LOCALE: tuple[str, str, str] = ("en", "US", "US:en")


def _locale_for_country(country: str | None) -> tuple[str, str, str]:
    """Return ``(hl, gl, ceid)`` for the given HQ country.

    Falls back to English-US when the country is unknown — a deliberate
    departure from the previous India-only default so non-Indian onboarded
    companies don't silently get India-filtered news.
    """
    if not country:
        return _GOOGLE_NEWS_DEFAULT_LOCALE
    return _GOOGLE_NEWS_LOCALES.get(country.strip(), _GOOGLE_NEWS_DEFAULT_LOCALE)
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


def fetch_google_news(
    query: str,
    max_results: int = 20,
    country: str | None = None,
) -> list[dict]:
    """Fetch Google News RSS for a search query.

    ``country`` is the company's ``headquarter_country``; it controls the
    ``hl`` / ``gl`` / ``ceid`` locale params so a German company gets
    German-language results instead of India-filtered ones (Phase 23A).
    """
    hl, gl, ceid = _locale_for_country(country)
    feed_url = GOOGLE_NEWS_URL.format(query=quote(query), hl=hl, gl=gl, ceid=ceid)
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
        # Best-effort hero image from RSS extensions. Many feeds expose
        # one of `media_content`, `media_thumbnail`, or an inline
        # `<img>` tag inside the summary.
        image_url = ""
        media_content = entry.get("media_content") or []
        if isinstance(media_content, list) and media_content:
            image_url = (media_content[0] or {}).get("url", "")
        if not image_url:
            media_thumb = entry.get("media_thumbnail") or []
            if isinstance(media_thumb, list) and media_thumb:
                image_url = (media_thumb[0] or {}).get("url", "")
        if not image_url and "<img" in (entry.get("summary") or ""):
            import re as _re
            m = _re.search(r'<img[^>]+src="([^"]+)"', entry.get("summary") or "")
            if m:
                image_url = m.group(1)
        articles.append(
            {
                "title": title,
                "summary": summary,
                "content": summary,  # RSS-only — no full content yet
                "source": source or "Google News",
                "url": url,
                "published_at": _parse_published(entry.get("published")),
                "metadata": {
                    "source_type": "google_news",
                    "image_url": image_url,
                },
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
                # Hero image — NewsAPI.org returns the OG/twitter image as
                # `urlToImage`. Surfaced via metadata so the UI cards and
                # newsletter hero get a real photo instead of a placeholder.
                "metadata": {
                    "source_type": "newsapi",
                    "image_url": item.get("urlToImage") or "",
                },
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

    # Accept either env-var name. Replit's secrets UI defaults to suffixing
    # `_API_KEY`, so legacy `NEWSAPI_AI_KEY` and `NEWSAPI_AI_API_KEY` (and the
    # generic Event Registry name) all resolve here. Without this, a key set
    # in Secrets silently no-ops and the orchestrator falls back to Google
    # News RSS — losing the full article body that makes HOME-tier scoring
    # possible.
    api_key = (
        os.environ.get("NEWSAPI_AI_KEY")
        or os.environ.get("NEWSAPI_AI_API_KEY")
        or os.environ.get("EVENT_REGISTRY_API_KEY")
        or ""
    )
    if not api_key:
        logger.debug("NewsAPI.ai: no API key in env (NEWSAPI_AI_KEY / NEWSAPI_AI_API_KEY / EVENT_REGISTRY_API_KEY)")
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
                    # Phase 9: image URL for newsletter rendering
                    "image_url": item.get("image") or "",
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


_RELEVANCE_HEAD_CHARS = 800  # window to scan at start of article body


# Phase 12.2 — wrap-up / daily-digest detection.
#
# Daily news wrap-ups bundle 5-10 unrelated stories into one article. If one
# of the stories briefly mentions a target company, the naïve relevance guard
# and event classifier both treat the wrap-up as an article *about* that
# company — producing hallucinated crisis narratives from text that was
# actually about an unrelated story in the same digest.
#
# Heuristic: a wrap-up has
#   (a) headline words like "wrap-up", "roundup", "weekly digest", OR
#   (b) ≤ 2 mentions of the target company AND 4+ other distinct
#       capitalised org names in the first 2 KB of body.
# If detected, we drop with `wrap_up` stat.
_WRAPUP_TITLE_MARKERS = (
    "wrap-up", "wrap up", "round-up", "round up", "roundup",
    "daily digest", "weekly digest", "morning digest", "evening digest",
    "top stories", "news briefs", "in brief", "news bites",
    "this week in", "daily news", "daily update",
)


def _is_wrapup_article(title: str, body: str, company: Company) -> bool:
    """Return True if the article looks like a daily digest / wrap-up that
    only mentions the target company in passing.

    The guard is intentionally conservative — we'd rather miss a few wrap-ups
    (and waste LLM budget on them) than incorrectly drop a legitimate deep-
    dive article that happens to reference other companies."""
    import re

    title_lower = (title or "").lower()
    if any(marker in title_lower for marker in _WRAPUP_TITLE_MARKERS):
        return True

    # If body is very short, no digest test applies
    if len(body or "") < 500:
        return False

    company_name_lower = (company.name or "").lower()
    if not company_name_lower:
        return False

    head_body = body[:2000].lower()
    company_mentions = head_body.count(company_name_lower)

    # Count distinct capitalised multi-word names that look like orgs.
    # Regex: 2-4 consecutive capitalised words, allowing Pvt / Ltd / Inc suffixes.
    org_pattern = re.compile(
        r"\b[A-Z][A-Za-z0-9&]{1,}(?:\s+[A-Z][A-Za-z0-9&]{1,}){1,3}\b"
    )
    orgs_in_head = {
        m.strip() for m in org_pattern.findall(body[:2000])
    }
    # Strip out the target company itself (and its slug tokens)
    company_tokens = set((company.name or "").lower().split())
    other_orgs = {
        o for o in orgs_in_head
        if (o.lower() != company_name_lower)
        and not all(t in o.lower() for t in company_tokens)
    }

    # A wrap-up has many distinct other orgs + the target company appears ≤ 2 times
    return len(other_orgs) >= 5 and company_mentions <= 2


# Phase 17 — Calendar-announcement / earnings-preview detector.
#
# Symptom (IDFC First Bank Q4 NDTV Profit, 2026-04-24): article title is
# "IDFC First Bank Q4 Results: Date, Time, Dividend News, Earnings Call
# Details And More" — a forward-looking calendar announcement carrying NO
# new earnings news. Body recycles last-quarter (Q3) numbers as background.
# The engine still scored it relevance=6 → HOME and ran the full LLM
# pipeline, producing speculation framed as analysis.
#
# Heuristic: a preview/calendar article has
#   (a) title containing one of the calendar markers below, AND
#   (b) body containing prior-quarter result language (Q1/Q2/Q3 + ₹ figures)
#       OR scheduling language ("to consider and approve", "earnings call
#       scheduled", "trading window closure").
# We intentionally drop these BEFORE the relevance scorer / event classifier
# so they never burn LLM budget. Rationale: the ESG signal in a calendar
# preview is zero — wait for the actual results press release instead.
import re as _calendar_re

_CALENDAR_TITLE_MARKERS = (
    # "Q4 Results: Date, Time" / "Q3 results date and time"
    _calendar_re.compile(r"\bq[1-4](?:fy\d{2,4})?\s+(?:results|earnings)\b.*\b(?:date|time|dividend|earnings call)\b", _calendar_re.IGNORECASE),
    # "earnings call details"
    _calendar_re.compile(r"\bearnings call\s+(?:details|date|time|schedule)\b", _calendar_re.IGNORECASE),
    # "results: when and where"
    _calendar_re.compile(r"\b(?:results|earnings)\s*:?\s*when\b", _calendar_re.IGNORECASE),
    # "...and more" + Q[N] in same title is almost always a preview
    _calendar_re.compile(r"\bq[1-4]\b.*\band more\b", _calendar_re.IGNORECASE),
)
_CALENDAR_BODY_PHRASES = (
    "to consider and approve",
    "trading window closure",
    "trading window is closed",
    "code of conduct for prohibition of insider trading",
    "earnings call scheduled",
    "earnings call with analysts",
    "board of directors is scheduled",
    "set to declare the financial results",
    "set to announce the financial results",
)


def _is_calendar_preview(title: str, body: str) -> bool:
    """Return True for forward-looking earnings-calendar / preview articles.

    These have zero new ESG signal — they just announce when the next
    results will be published. Live-fail example was the IDFC NDTV Profit
    Q4 announcement (2026-04-24) which the engine misclassified as a Q3
    earnings reveal, then hallucinated "190.5 bps margin compression" off
    of recycled Q3 numbers.
    """
    if not title:
        return False
    if not any(rx.search(title) for rx in _CALENDAR_TITLE_MARKERS):
        return False
    body_low = (body or "").lower()
    return any(phrase in body_low for phrase in _CALENDAR_BODY_PHRASES)


def _is_article_about_company(title: str, body: str, company: Company) -> bool:
    """Relevance guard: does this article actually mention the target company?

    NewsAPI.ai keyword search returns articles that contain the query phrase
    *anywhere* in 2-5 KB of body text — fine for coverage, awful for precision.
    A "JSW Energy" query will happily return an article about JSW Steel that
    happens to use the word "energy" in a sibling sentence.

    The fix is a phrase-level check: the company's full name (case-insensitive,
    whitespace-normalised) must appear either in the title or in the first
    ~800 chars of the body. That's restrictive enough to drop sibling-company
    false positives but loose enough to keep articles that mention the company
    up-front and then pivot to a broader sector theme.

    Returns True if the article is meaningfully about the company.
    """
    import re

    needle = re.sub(r"\s+", " ", (company.name or "").strip().lower())
    if not needle:
        return True  # no guard possible, let it through

    # Normalise whitespace in haystack too — handles "JSW\nEnergy" line-wrap
    title_norm = re.sub(r"\s+", " ", (title or "").lower())
    head_norm = re.sub(r"\s+", " ", (body or "")[:_RELEVANCE_HEAD_CHARS].lower())

    return needle in title_norm or needle in head_norm


def fetch_for_company(
    company: Company,
    max_per_query: int | None = None,
    persist: bool = True,
) -> list[IngestedArticle]:
    """Fetch news for one company across all configured queries.

    Phase 1 gating applied in order:
      1. URL-hash dedup (identical URL already processed)
      2. Company-relevance guard (phrase match in title or first 800 chars)
      3. Freshness gate (published_at within configured age window)
      4. Semantic dedup (near-duplicate title+summary within rolling window)
    """
    settings = load_settings()
    ingestion_cfg = settings.get("ingestion", {})
    limit = max_per_query or ingestion_cfg.get(
        "max_articles_per_company_per_run", 20
    )
    freshness_days = ingestion_cfg.get("freshness_max_age_days", 90)
    sem_enabled = ingestion_cfg.get("semantic_dedup_enabled", True)
    sem_threshold = ingestion_cfg.get("semantic_dedup_threshold", 0.75)
    sem_window = ingestion_cfg.get("semantic_dedup_window_hours", 48)

    processed = _load_processed()

    raw_articles: list[dict] = []
    seen_urls: set[str] = set()
    hq_country = getattr(company, "headquarter_country", None)
    for query in company.news_queries:
        # Phase 24 — NewsAPI.ai is the primary source (full article body,
        # 2,000-5,000+ chars). Google News RSS is only used as a fallback
        # when NewsAPI.ai returns zero results for this query (key
        # missing, rate-limited, or genuinely no matches). NewsAPI.org
        # was removed: it added a third API call per query for marginal
        # extra coverage and frequent paywall-snippet noise.
        primary = fetch_newsapi_ai(query, max_results=limit)
        if primary:
            for art in primary:
                if art["url"] in seen_urls:
                    continue
                seen_urls.add(art["url"])
                art.setdefault("source_type", "newsapi_ai")
                art["query"] = query
                raw_articles.append(art)
            continue

        # Fallback path: Google News RSS with HQ-country locale (Phase 23A).
        logger.info(
            "news_fetcher: NewsAPI.ai returned 0 for %r — falling back to Google News RSS",
            query,
        )
        for art in fetch_google_news(query, max_results=limit, country=hq_country):
            if art["url"] in seen_urls:
                continue
            seen_urls.add(art["url"])
            art.setdefault("source_type", "google_news")
            art["query"] = query
            raw_articles.append(art)

    # Phase 1: semantic dedup across all sources/queries for this company
    dedup = SemanticDedup(threshold=sem_threshold, window_hours=sem_window) if sem_enabled else None

    fresh: list[IngestedArticle] = []
    stats = {"stale": 0, "semantic_dup": 0, "url_dup": 0, "off_topic": 0, "wrap_up": 0, "calendar_preview": 0}
    for raw in raw_articles:
        h = _url_hash(raw["url"])
        if h in processed:
            stats["url_dup"] += 1
            continue

        # Phase 12.2: wrap-up / daily-digest guard — reject articles that
        # bundle multiple unrelated stories. These fool the event classifier
        # into picking events from sibling stories, causing hallucinated
        # crisis narratives.
        if _is_wrapup_article(raw.get("title") or "", raw.get("content") or "", company):
            stats["wrap_up"] += 1
            logger.debug(
                "wrap-up article skipped: %r for %s",
                (raw.get("title") or "")[:80],
                company.slug,
            )
            continue

        # Phase 17: calendar-announcement / earnings-preview guard. These are
        # forward-looking "Q4 results due Apr 25" articles with zero new ESG
        # signal — they recycle prior-quarter numbers as context, fooling the
        # relevance scorer into scoring them HOME. Drop them before they reach
        # the LLM. Live-fail example: IDFC NDTV Q4 calendar (2026-04-24).
        if _is_calendar_preview(raw.get("title") or "", raw.get("content") or ""):
            stats["calendar_preview"] += 1
            logger.debug(
                "calendar-preview article skipped: %r for %s",
                (raw.get("title") or "")[:80],
                company.slug,
            )
            continue

        # Company-relevance guard — NewsAPI.ai's keyword search is permissive
        # enough that "JSW Energy" query returns JSW Steel articles. The
        # phrase-match check below catches those before they waste LLM budget
        # on mis-attributed analyses.
        if not _is_article_about_company(raw.get("title") or "", raw.get("content") or "", company):
            stats["off_topic"] += 1
            logger.debug(
                "off-topic article skipped: %r not in title/head for %s",
                (raw.get("title") or "")[:60],
                company.slug,
            )
            continue

        # Freshness gate
        if not is_fresh(raw, max_age_days=freshness_days):
            stats["stale"] += 1
            logger.debug(
                "stale article skipped: %s (published %s)",
                (raw.get("title") or "")[:60],
                raw.get("published_at"),
            )
            continue

        # Semantic dedup
        if dedup is not None:
            is_dup, _ = dedup.is_duplicate(raw)
            if is_dup:
                stats["semantic_dup"] += 1
                continue

        processed.add(h)
        # Preserve the fetcher's metadata (image_url, sentiment, concepts, etc.)
        # and add the query that matched. The old code overwrote the whole
        # metadata dict which is why newsletter hero images were never populated.
        merged_metadata = dict(raw.get("metadata") or {})
        merged_metadata["query"] = raw.get("query", "")
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
            metadata=merged_metadata,
        )
        fresh.append(article)
        if persist:
            _write_article(article)

    if persist and fresh:
        _save_processed(processed)
    logger.info(
        "news_fetcher: %s -> fetched %s, new %s (stale %s, url_dup %s, semantic_dup %s, off_topic %s, wrap_up %s, calendar_preview %s)",
        company.slug,
        len(raw_articles),
        len(fresh),
        stats["stale"],
        stats["url_dup"],
        stats["semantic_dup"],
        stats["off_topic"],
        stats["wrap_up"],
        stats["calendar_preview"],
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
