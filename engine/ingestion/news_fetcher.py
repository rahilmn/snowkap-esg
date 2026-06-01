"""Fetch ESG news for target companies.

Phase 48.A — NewsAPI.ai (EventRegistry) is the SOLE source. One complex
query per company (company name AND any ESG term, last 30 days, newest
first) returns full article bodies + hero images directly. Google News
RSS and the publisher-scrape full-text backfill have been removed.

Outputs normalized JSON files to ``data/inputs/news/{company_slug}/``
with deduplication tracked in ``data/processed/article_hashes.json``.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
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


# Phase 48.A — fetch_google_news() removed. NewsAPI.ai is the sole source.
# See fetch_newsapi_ai_for_company() below.


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

    # Phase 24.1 — NewsAPI.ai treats a multi-word ``keyword`` STRING as a
    # literal phrase ("Adani Power ESG" only matches articles containing
    # that exact 4-word sequence — typically zero hits for our composed
    # queries). Pass each whitespace-separated token as a list element
    # with ``keywordOper:'and'`` so all words must appear somewhere in
    # the article. Single-word queries pass through as-is.
    tokens = [t for t in query.split() if t]
    if len(tokens) > 1:
        keyword_payload: Any = tokens
        keyword_oper = "and"
    else:
        keyword_payload = query
        keyword_oper = None

    try:
        body: dict[str, Any] = {
            "action": "getArticles",
            "keyword": keyword_payload,
            "articlesPage": 1,
            "articlesCount": min(max_results, 10),  # conserve free tier tokens
            "articlesSortBy": "date",
            "includeArticleBody": True,
            "articleBodyLen": -1,  # full body
            "resultType": "articles",
            "lang": "eng",
            "apiKey": api_key,
        }
        if keyword_oper:
            body["keywordOper"] = keyword_oper
        resp = requests.post(NEWSAPI_AI_URL, json=body, timeout=20)
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

    # Phase 5 wiring — record the spend in the central NewsRouter budget so
    # the /metrics endpoint's snowkap_newsapi_budget series shows real
    # numbers and an operator can see Tier 1 consumption in real time.
    # ASSUMPTION (per news_router.py): 1 token = 1 article in response.
    # If the verified rule turns out to be different, swap the budget's
    # token_cost_fn — this site does NOT need to change.
    try:
        from engine.ingestion.news_router import get_router
        router = get_router()
        router.budget.spend(router.token_cost_fn(articles))
    except Exception:  # noqa: BLE001 — budget tracking must never break ingest
        pass

    return articles


# ---------------------------------------------------------------------------
# Phase 48.B — token-efficient, ESG-focused, ONE-call-per-company fetch
# ---------------------------------------------------------------------------

# ESG / sustainability keyword universe. The complex NewsAPI.ai query
# requires "company name" AND (any one of these). Keep these ESG-PRECISE:
# bare finance words ("governance", "compliance", "investment") over-match
# generic market/stock coverage, so we use specific multi-word phrases and
# framework codes that only appear in genuine ESG/sustainability stories.
# This keeps the single ~18-token call focused on real ESG signal.
_ESG_KEYWORDS: tuple[str, ...] = (
    "ESG", "sustainability", "sustainable finance", "climate change",
    "climate risk", "emissions", "carbon", "net zero", "decarbonisation",
    "decarbonization", "renewable energy", "clean energy", "green bond",
    "green finance", "CSRD", "ESRS", "BRSR", "TCFD", "GRI", "CBAM",
    "Scope 3", "greenhouse gas", "ESG rating", "sustainability report",
    "transition plan", "biodiversity", "corporate governance",
    "human rights", "circular economy", "energy transition",
)

# Phase 49.2 — wider ESG net for niche tenants whose names rarely headline an
# India-ESG story (boutique asset managers, foreign auto-parts suppliers).
# Adds sector-appropriate sustainability vocabulary (responsible investment /
# stewardship for AMCs; EV-transition / supply-chain-due-diligence for auto
# suppliers) plus core governance/compliance terms. Only applied to companies
# listed in settings `ingestion.broad_query_companies`; the strict set still
# governs the banks/energy tenants. The downstream company-relevance guard
# (`_is_article_about_company`) + wrap-up guard + Stage 3-4 ontology relevance
# ranking remain the precision anchors, so a wider keyword net never lets a
# roundup or an off-company story onto the deck.
# NOTE: EventRegistry counts each WORD of a multi-word keyword toward the
# subscription keyword limit (80 on the current plan). The strict set above is
# ~47 words; appending the full sector vocabulary pushed the broad query to 93
# words and the API rejected it ("Too many keywords specified") — returning
# zero. So the broad set is a LEAN, curated standalone tuple (~46 words),
# single-word-first, spanning the asset-management + automotive lenses, kept
# well under the limit. The company-identity clause (a precise conceptUri or
# alias $or) does most of the scoping; this $or just keeps the focus on
# sustainability/governance content.
_ESG_KEYWORDS_BROAD: tuple[str, ...] = (
    "ESG", "sustainability", "emissions", "carbon", "net zero",
    "decarbonisation", "renewable energy", "clean energy", "green bond",
    "CSRD", "ESRS", "BRSR", "TCFD", "CBAM", "Scope 3", "climate risk",
    "energy transition", "biodiversity", "governance", "human rights",
    "circular economy", "electric vehicle", "supply chain", "powertrain",
    "thermal management", "REACH", "PFAS", "responsible investment",
    "stewardship", "sustainable investing", "disclosure", "compliance",
)


def _broad_query_slugs() -> set[str]:
    """Slugs whose NewsAPI.ai query should drop the company-in-TITLE lock.

    Read from settings `ingestion.broad_query_companies`. These are niche
    tenants (boutique AMC, foreign auto-parts supplier) whose names rarely
    appear in an India-ESG article title — the title-lock starves their deck.
    """
    try:
        cfg = load_settings().get("ingestion", {})
        return {str(s).strip().lower() for s in (cfg.get("broad_query_companies") or [])}
    except Exception:  # noqa: BLE001 — config read must never break a fetch
        return set()


def _company_keyword(company: Company) -> str:
    """Short, search-friendly company name (strip legal suffixes)."""
    name = (company.name or company.slug or "").strip()
    for suffix in (
        " Limited", ", Inc.", " Inc.", " PLC", " Plc", " SE", " AG",
        " Ltd", " Ltd.", " GmbH", " Corporation", " Corp.", " Company",
    ):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
    return name or company.slug


def fetch_newsapi_ai_for_company(
    company: Company,
    max_results: int = 18,
    freshness_days: int = 30,
    strict_title: bool | None = None,
) -> list[dict]:
    """ONE NewsAPI.ai call per company — company name AND any ESG term,
    within the last `freshness_days`, newest first, full body + image.

    Token-frugal: a single getArticles request returning ~`max_results`
    articles costs ~`max_results` tokens (vs 10-11 keyword calls). The
    complex `$query` enforces the ESG focus server-side and `dateStart`
    enforces the 1-month freshness window server-side (so we never pay
    tokens for stale or off-topic content).

    `strict_title` controls whether the company keyword must appear in the
    article TITLE (precise, kills market-roundup noise — the default for the
    banks/energy tenants) or merely anywhere in title+body (broader, for
    niche tenants whose names rarely headline an India-ESG story). When
    ``None`` it is resolved per-company from settings
    `ingestion.broad_query_companies`. The broad path also widens the ESG
    keyword set (`_ESG_KEYWORDS_BROAD`). Precision is still enforced
    downstream by `_is_article_about_company` (company must appear in the
    title or first ~800 chars of body) + the wrap-up guard, so dropping the
    title-lock never admits roundups or off-company stories.
    """
    api_key = (
        os.environ.get("NEWSAPI_AI_KEY")
        or os.environ.get("NEWSAPI_AI_API_KEY")
        or os.environ.get("EVENT_REGISTRY_API_KEY")
        or ""
    )
    if not api_key:
        logger.error(
            "NewsAPI.ai key missing (NEWSAPI_AI_KEY / NEWSAPI_AI_API_KEY / "
            "EVENT_REGISTRY_API_KEY) — cannot fetch for %s", company.slug,
        )
        return []

    keyword = _company_keyword(company)
    date_start = (datetime.now(timezone.utc).date()
                  - timedelta(days=freshness_days)).isoformat()
    date_end = datetime.now(timezone.utc).date().isoformat()

    # Resolve strict-title vs broad per-company. Broad tenants (niche AMC,
    # foreign auto-parts supplier) drop the title-lock and widen the ESG net
    # so their decks aren't starved; the banks/energy tenants stay strict.
    if strict_title is None:
        strict_title = (company.slug or "").strip().lower() not in _broad_query_slugs()
    esg_terms = _ESG_KEYWORDS if strict_title else _ESG_KEYWORDS_BROAD

    # STRICT path: company name must appear in the TITLE (the article is
    # genuinely ABOUT the company, not a multi-stock roundup that merely lists
    # it), AND an ESG term must appear anywhere. This kills the market-roundup
    # noise that plagues financial-sector tenants while keeping real
    # sustainability coverage.
    # BROAD path: company name anywhere (title OR body) AND a (wider) ESG term
    # anywhere — for niche tenants whose names rarely headline an India-ESG
    # story. Precision is recovered downstream by `_is_article_about_company`
    # (company must be in the title or first ~800 chars) + the wrap-up guard +
    # the Stage 2-4 ontology ESG-materiality ranking.
    # Phase 49.2 — data-driven company-identity clause. For ambiguous names
    # (e.g. "MAHLE" collides with a baseball player + the composer Mahler) the
    # most precise match is the EventRegistry *concept* (entity URI), seeded
    # on the company row as `news_concept_uri`. Some tenants also carry
    # `news_aliases` (e.g. an AMC + its founder) to widen identity matching.
    # Both live in company data — NOT hardcoded in Python. Falls back to the
    # suffix-stripped keyword (title-locked when strict).
    cal = company.primitive_calibration or {}
    concept_uri = str(cal.get("news_concept_uri") or "").strip()
    aliases = [a.strip() for a in (cal.get("news_aliases") or [])
               if isinstance(a, str) and a.strip()]
    if concept_uri:
        identity_clause: dict[str, Any] = {"conceptUri": concept_uri}
    elif aliases and not strict_title:
        identity_clause = {"$or": [{"keyword": a} for a in aliases]}
    else:
        identity_clause = {"keyword": keyword}
        if strict_title:
            identity_clause["keywordLoc"] = "title"
    complex_query: dict[str, Any] = {
        "$query": {
            "$and": [
                identity_clause,
                {"$or": [{"keyword": kw} for kw in esg_terms]},
                {"lang": "eng"},
            ],
            "dateStart": date_start,
            "dateEnd": date_end,
        },
    }
    body: dict[str, Any] = {
        "action": "getArticles",
        "query": complex_query,
        "resultType": "articles",
        "articlesPage": 1,
        "articlesCount": min(max_results, 50),
        "articlesSortBy": "date",
        "includeArticleBody": True,
        "articleBodyLen": -1,
        "includeArticleImage": True,
        "apiKey": api_key,
    }

    try:
        resp = requests.post(NEWSAPI_AI_URL, json=body, timeout=25)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("NewsAPI.ai company fetch failed for %s: %s", company.slug, exc)
        return []

    payload = resp.json()
    results = (payload.get("articles") or {}).get("results") or []
    articles: list[dict] = []
    for item in results:
        url = item.get("url") or ""
        if not url:
            continue
        body_text = item.get("body") or ""
        title = item.get("title") or ""
        source_name = (item.get("source") or {}).get("title") or "NewsAPI.ai"
        published = item.get("dateTime") or item.get("date") or ""
        articles.append({
            "title": _strip_html(title),
            "summary": _strip_html(body_text[:500]) if body_text else title,
            "content": _strip_html(body_text),
            "source": source_name,
            "url": url,
            "published_at": _parse_published(published),
            "source_type": "newsapi_ai",
            "metadata": {
                "sentiment": item.get("sentiment"),
                "source_type": "newsapi_ai",
                "image_url": item.get("image") or "",
                "concepts": [
                    (c.get("label") or {}).get("eng", "")
                    for c in (item.get("concepts") or [])[:5]
                ],
            },
        })

    logger.info(
        "NewsAPI.ai: %d ESG articles for %s (keyword=%r, since=%s, avg %d chars)",
        len(articles), company.slug, keyword, date_start,
        sum(len(a["content"]) for a in articles) // max(len(articles), 1),
    )

    # Record spend in the central budget (1 token ≈ 1 returned article).
    try:
        from engine.ingestion.news_router import get_router
        router = get_router()
        router.budget.spend(router.token_cost_fn(articles))
    except Exception:  # noqa: BLE001 — budget tracking must never break ingest
        pass

    return articles


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


_RELEVANCE_HEAD_CHARS = 2000  # window to scan at start of article body. Bumped 800→2000
# because sector / market-roundup pieces frequently mention the company in the
# first 2-3 paragraphs but past the 800-char mark, and rejecting those rows
# was leaving fresh onboards with 0 articles on the home page.


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

# Phase 49.2 — MARKET ROUNDUP markers. Multi-stock market pieces ("5 Adani
# stocks log 52-week highs", "8 Other Stocks MFs bought", "ICICI Bank Opening
# Bell Updates", "Motilal Oswal sector of the week ... top bets") name the
# target company in the TITLE but carry NO single-company ESG signal. They
# slipped past the company-in-title early-return in `_is_wrapup_article` and
# were promoted to CRITICAL with a fabricated ESG lede + recs + a generic
# "developing story" summary (the live Adani + ICICI + JSW cards). These are
# market noise, not ESG news — drop them regardless of company-in-title.
_MARKET_ROUNDUP_MARKERS = (
    "opening bell", "closing bell", "market wrap", "sensex today",
    "nifty today", "top bets", "top picks", "top buys", "top gainers",
    "top losers", "sector of the week", "stocks to buy", "stocks to watch",
    "stocks to sell", "buy or sell", "stock radar", "stocks in focus",
    "stocks to track", "52-week high", "52 week high", "52-week low",
    "multibagger", "other stocks", "stocks in which", "f&o ban",
    "muhurat", "trade setup", "stocks to add",
    # Phase 49.2 — brokerage price-target / share-price-ticker / stock-pick /
    # stock-comparison noise (non-ESG market content that cluttered the LIGHT
    # tier). High-precision: each requires price/rs/stock adjacency so genuine
    # ESG "target" headlines ("net-zero target by 2050") are NOT caught.
    "share price target", "price target", "target of rs", "target at rs",
    "share price today", "share price live", "share price - live",
    "stock price today", "stock picks", "stocks today", "stock offers better",
    "bullish view", "bearish view", "retains buy", "reiterates buy",
    "share price target at", "stock to buy or",
)


def _is_market_roundup(title_lower: str) -> bool:
    """Return True for multi-stock market roundups / daily-market updates that
    name the company but carry no single-company ESG signal."""
    import re
    if not title_lower:
        return False
    if any(m in title_lower for m in _MARKET_ROUNDUP_MARKERS):
        return True
    # "<N> [adani] stocks ..." numeric-stock-list pattern
    if re.search(r"\b\d+\s+(?:[a-z&]+\s+){0,2}stocks?\b", title_lower):
        return True
    if re.search(r"\bstocks?\s+(?:that|to|in which|for)\b", title_lower):
        return True
    return False


def _is_wrapup_article(title: str, body: str, company: Company) -> bool:
    """Return True if the article looks like a daily digest / wrap-up that
    only mentions the target company in passing.

    The guard is intentionally conservative — we'd rather miss a few wrap-ups
    (and waste LLM budget on them) than incorrectly drop a legitimate deep-
    dive article that happens to reference other companies."""
    import re

    title_lower = (title or "").lower()

    # Phase 49.2 — MARKET ROUNDUP guard runs FIRST, before the company-in-title
    # early-return below. A multi-stock roundup ("5 Adani stocks ...", "ICICI
    # Bank Opening Bell ...", "... top bets") names the company in the title but
    # is market noise with no single-company ESG signal. These were the live
    # Adani + ICICI + JSW "criticals" with fabricated ledes/recs. Drop them.
    if _is_market_roundup(title_lower):
        return True

    # Phase 48.A — the NewsAPI.ai fetch already requires the company keyword
    # in the TITLE, so a fetched article is genuinely ABOUT the company, not
    # a passing-mention digest. If the company short-name is in the title,
    # this is not a wrap-up — return early. This stops the guard from
    # dropping long single-company pieces (e.g. earnings-call transcripts)
    # whose bodies happen to name many analysts/banks/orgs, and handles the
    # name-mismatch case where company.name ("MAHLE GmbH") differs from the
    # body subject ("Mahle Metal Leve").
    short = _company_keyword(company).lower()
    if short and short in title_lower:
        return False

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


_CORP_SUFFIX_RE = re.compile(
    r"\s+(limited|ltd|plc|inc|corp|corporation|company|co|llc|"
    r"ag|se|gmbh|nv|s\.?a\.?|s\.?p\.?a\.?|kg|kgaa|pvt|"
    r"holdings|group|industries|enterprises|international|"
    r"aktiengesellschaft|société\s+anonyme|sa)\.?\s*$",
    re.IGNORECASE,
)


def _company_name_variants(name: str) -> list[str]:
    """Return the canonical name + a short-form with corporate suffix stripped.

    Most news headlines say "Infosys" not "Infosys Limited", "Tata Motors" not
    "Tata Motors Limited", "Siemens" not "Siemens Aktiengesellschaft". The
    suffix-stripped variant is what 90% of articles actually use.

    Deduped, lowercase, whitespace-normalised. Empty inputs produce [].
    """
    import re as _re

    canonical = _re.sub(r"\s+", " ", (name or "").strip().lower())
    if not canonical:
        return []
    variants: list[str] = [canonical]
    short = _CORP_SUFFIX_RE.sub("", canonical).strip()
    if short and short != canonical and len(short) >= 3:
        variants.append(short)
    return variants


def _is_article_about_company(title: str, body: str, company: Company) -> bool:
    """Relevance guard: does this article actually mention the target company?

    NewsAPI.ai + Google News keyword search returns articles that contain the
    query phrase *anywhere* in 2-5 KB of body text — fine for coverage, awful
    for precision. A "JSW Energy" query will happily return an article about
    JSW Steel that happens to use the word "energy" in a sibling sentence.

    The fix is a phrase-level check against EITHER the canonical name or the
    suffix-stripped short form ("Infosys" matches even when the official name
    is "Infosys Limited"). One of those variants must appear in the title or
    the first ~800 chars of the body. Restrictive enough to drop sibling-
    company false positives, loose enough to keep typical headline wording.

    Returns True if the article is meaningfully about the company.
    """
    import re

    variants = _company_name_variants(company.name or "")
    if not variants:
        return True  # no guard possible, let it through

    title_norm = re.sub(r"\s+", " ", (title or "").lower())
    head_norm = re.sub(r"\s+", " ", (body or "")[:_RELEVANCE_HEAD_CHARS].lower())

    return any(v in title_norm or v in head_norm for v in variants)


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
    freshness_days = ingestion_cfg.get("freshness_max_age_days", 30)
    sem_enabled = ingestion_cfg.get("semantic_dedup_enabled", True)
    sem_threshold = ingestion_cfg.get("semantic_dedup_threshold", 0.75)
    sem_window = ingestion_cfg.get("semantic_dedup_window_hours", 48)

    processed = _load_processed()

    raw_articles: list[dict] = []
    seen_urls: set[str] = set()
    # Phase 48.A — NewsAPI.ai is the SOLE source. Google News RSS and the
    # publisher-scrape full-text backfill are gone. ONE complex NewsAPI.ai
    # call per company (company name AND any ESG term, last `freshness_days`,
    # newest first) returns full bodies + hero images directly — no
    # scraping, no googlenewsdecoder, no per-query fan-out. Token cost is
    # ~`limit` tokens per company per fetch.
    newsapi_articles = fetch_newsapi_ai_for_company(
        company, max_results=limit, freshness_days=freshness_days,
    )
    for art in newsapi_articles:
        url = art.get("url") or ""
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        art.setdefault("source_type", "newsapi_ai")
        art["query"] = _company_keyword(company)
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
            source_type=raw.get("source_type", "newsapi_ai"),
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
