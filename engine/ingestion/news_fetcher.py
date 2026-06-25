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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Allow `python -m engine.ingestion.news_fetcher` without PYTHONPATH.
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.config import Company, get_company, get_data_path, get_newsapi_key, load_companies, load_settings
from engine.ingestion.dedup import SemanticDedup, is_fresh

logger = logging.getLogger(__name__)

NEWSAPI_URL = "https://newsapi.org/v2/everything"

# Phase 51.C — shared HTTP session with bounded retries + backoff so a
# transient NewsAPI.ai 429 / 5xx / connection blip doesn't silently lose a
# company's weekly fetch. Retries fire ONLY on no-data failures
# (429/5xx/connection), so they never re-spend NewsAPI tokens — tokens are
# charged per article returned on a 200. Honors Retry-After on 429. The
# NewsAPI.ai query POST is read-only (a search), so retrying it is safe.
_HTTP_RETRY = Retry(
    total=int(os.environ.get("SNOWKAP_HTTP_MAX_RETRIES", "2")),
    backoff_factor=0.6,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET", "POST"]),
    respect_retry_after_header=True,
    raise_on_status=False,
)


def _build_http_session() -> requests.Session:
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=_HTTP_RETRY)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


_SESSION = _build_http_session()


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
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "hashes": sorted(hashes)}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        # Dedup cache is best-effort; on a read-only data dir we skip persisting
        # it (Postgres article_pool de-dupes by URL anyway). Non-fatal.
        logger.warning("news_fetcher: could not persist dedup cache (non-fatal): %s", exc)


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


def _write_article(article: IngestedArticle) -> Path | None:
    date_prefix = article.published_at[:10]  # YYYY-MM-DD
    folder = get_data_path("inputs", "news", article.company_slug)
    filename = f"{date_prefix}_{article.id}.json"
    path = folder / filename
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(article), indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        # Read-only / non-writable data dir (e.g. Railway without a mounted
        # volume): the article already flows to the pipeline (and Postgres via
        # article_pool), so the on-disk reprocessing copy is optional. Logging
        # and continuing keeps onboarding + the weekly refresh working.
        logger.warning("news_fetcher: could not persist %s to disk (non-fatal): %s", path, exc)
        return None
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
        resp = _SESSION.post(NEWSAPI_AI_URL, json=body, timeout=20)
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

# Phase 52 — ESG-MATERIAL / harm vocabulary for the SECOND, body-matched fetch.
# The strict sets above are dominated by generic ESG framing ("ESG",
# "sustainability", "net zero") that market PR sprinkles into stock coverage, so
# for market-heavy names (power/renewable) the title-locked primary query
# returns 0 critical ESG events. This tuple is HARM/ENFORCEMENT-weighted and
# India-regulator-aware — penalty/violation/spill/coal/displacement/NGT/CPCB —
# so the 2nd query surfaces SUBSTANTIVE ESG/negative stories (where the company
# sits in the body), not green-PR noise. Lean + single-word-first: EventRegistry
# counts every WORD against the 80-word plan limit; this set is ~40 words.
_ESG_KEYWORDS_MATERIAL: tuple[str, ...] = (
    # Environmental harm
    "emissions", "pollution", "coal", "effluent", "spill", "contamination",
    "groundwater", "deforestation", "hazardous waste", "emission norms",
    "environmental clearance", "oil spill",
    # Enforcement / governance
    "penalty", "fine", "violation", "show cause", "non-compliance",
    "regulatory action", "tribunal", "NGT", "CPCB",
    # Social harm
    "displacement", "eviction", "land acquisition", "protest", "human rights",
    "child labour", "labour", "safety", "fatality", "rehabilitation",
)


# ---------------------------------------------------------------------------
# Phase 56.C — COMPOSED ESG-material vocabulary (retrieval; content authored
# separately). The single static _ESG_KEYWORDS_MATERIAL above is a heavy-
# industry / pollution lexicon (coal, effluent, NGT, deforestation) — for an
# EV/auto or services name it searches the wrong words, the ESG-material lane
# returns ~nothing, and the deck falls back to company-name market news. The
# 2nd-fetch vocab is now COMPOSED per company as a UNION of layers, keyed off the
# resolved SASB sector + the jurisdiction, so the right ESG-event terms fall out
# automatically. The two overlay dicts ship EMPTY on purpose; until seeded, every
# company composes to base-only and fires the loud-miss warning.
# ---------------------------------------------------------------------------

# Universal harm terms — apply to EVERY sector/jurisdiction. Never dropped: the
# sector/jurisdiction overlays and any per-tenant override ADD to this base.
_ESG_HARM_BASE: tuple[str, ...] = (
    "penalty", "fine", "recall", "lawsuit", "settlement", "sanction",
    "strike", "layoff", "injury", "pollution", "emission", "contamination",
    "data breach", "governance failure",
)

# Sector overlay — keyed by the SASB sector label from _sasb_sector_for() (NOT
# company.sasb_category, which is the literal "Unknown" in prod and would silently
# miss). EMPTY by design; content authored separately. Real key space = the
# values of INDUSTRY_TO_SASB_DEFAULT (e.g. "Automobiles", "Commercial Banks").
_SECTOR_ESG_VOCAB: dict[str, tuple[str, ...]] = {}

# Jurisdiction overlay — keyed by framework_region upper-cased (INDIA / EU / UK /
# US / APAC / GLOBAL). EMPTY by design; content authored separately.
_JURISDICTION_REGULATORS: dict[str, tuple[str, ...]] = {}


def _compose_esg_material(company: Company, override=None) -> tuple[str, ...]:
    """Compose the ESG-material 2nd-fetch vocab as a UNION of layers:

        _ESG_HARM_BASE
        ∪ _SECTOR_ESG_VOCAB.get(_sasb_sector_for(company), ())
        ∪ _JURISDICTION_REGULATORS.get((framework_region or "GLOBAL").upper(), ())
        ∪ (override or ())

    The override ADDS to the base — it never replaces it; a tenant adding one
    term keeps penalty/fine/recall/lawsuit. The sector lookup keys off
    ``_sasb_sector_for(company)`` (the fallback-aware helper) because the stored
    ``company.sasb_category`` is the literal "Unknown" in prod and would silently
    miss. A missing sector OR jurisdiction overlay is logged LOUDLY and routed to
    the coverage-assertion stream — an unseeded overlay must be observable, never
    a silent empty .get() (that is the original starvation bug rebuilt a layer
    down).
    """
    sector = _sasb_sector_for(company)
    region = (getattr(company, "framework_region", "") or "GLOBAL").upper()
    sector_overlay = _SECTOR_ESG_VOCAB.get(sector, ())
    region_overlay = _JURISDICTION_REGULATORS.get(region, ())

    if not sector_overlay or not region_overlay:
        # Coverage-assertion signal — consumed by the per-company coverage probe
        # (probe > 0 ESG hits but deck ESG = 0 => retrieval miss). NOT swallowed.
        logger.warning(
            "coverage_assertion.esg_overlay_miss sector=%s region=%s company=%s "
            "sector_seeded=%s region_seeded=%s -> base-only vocab",
            sector or "?", region, getattr(company, "slug", "?"),
            bool(sector_overlay), bool(region_overlay),
        )

    override_terms = tuple(
        str(k).strip() for k in (override or ()) if str(k).strip()
    )
    # Union, de-duped case-insensitively, base-first so the universal harm terms
    # always lead the query.
    out: list[str] = []
    seen: set[str] = set()
    for term in (*_ESG_HARM_BASE, *sector_overlay, *region_overlay, *override_terms):
        key = term.lower()
        if key not in seen:
            seen.add(key)
            out.append(term)
    return tuple(out)


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


def _esg_second_fetch_enabled(company: Company) -> bool:
    """Phase 52 — whether to fire the SECOND, body-matched ESG-material query.

    Per-company override via ``primitive_calibration.esg_second_fetch`` (mirrors
    the existing ``news_no_esg_filter`` pattern):
      * ``"off"``  → never (e.g. a bank whose strict path already fills its deck);
      * ``"on"``   → always (force for a chronically ESG-starved company);
      * ``"auto"`` (default) → only when the NewsAPI budget has comfortable
        headroom, so the extra query can never silently blow the monthly cap.
    """
    cal = getattr(company, "primitive_calibration", None) or {}
    mode = str(cal.get("esg_second_fetch", "auto")).strip().lower()
    if mode in ("off", "false", "0", "no"):
        return False
    if mode in ("on", "true", "1", "yes"):
        return True
    # auto — budget-floor gate
    try:
        from engine.ingestion.news_router import get_router
        floor = int(os.environ.get("SNOWKAP_ESG_FETCH_MIN_REMAINING", "600"))
        return get_router().budget.remaining() > floor
    except Exception:  # noqa: BLE001 — fail-open: an ESG-starved deck is worse
        return True


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
    esg_keywords: tuple[str, ...] | None = None,
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
    # Phase 52 — an explicit `esg_keywords` override (the ESG-material vocab on
    # the second, body-matched fetch) wins; otherwise pick by strict/broad.
    esg_terms = esg_keywords if esg_keywords is not None else (
        _ESG_KEYWORDS if strict_title else _ESG_KEYWORDS_BROAD
    )

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
    # Phase 49.3 — `news_no_esg_filter` drops the ESG-term AND-clause for a
    # tenant whose genuine coverage is real but NOT ESG-tagged (a boutique AMC
    # whose only news is its own fund activity). Identity-only query so the
    # deck isn't empty; the cards are genuine company news (just light-tier,
    # low ESG materiality). Off by default — set explicitly per tenant.
    no_esg_filter = bool(cal.get("news_no_esg_filter"))
    if concept_uri:
        identity_clause: dict[str, Any] = {"conceptUri": concept_uri}
    elif aliases:
        # Phase 51.D — title-match ANY alias (e.g. "State Bank of India" OR the
        # common acronym "SBI") so acronym headlines aren't missed. Stays
        # title-locked under strict_title, so precision (no market-roundup
        # noise) is preserved. Previously aliases were honoured ONLY when
        # non-strict, so strict banks/energy tenants never benefited from an
        # acronym alias — that's why SBI ("State Bank of India") under-fetched.
        _loc = {"keywordLoc": "title"} if strict_title else {}
        identity_clause = {"$or": [{"keyword": a, **_loc} for a in aliases]}
    else:
        identity_clause = {"keyword": keyword}
        if strict_title:
            identity_clause["keywordLoc"] = "title"
    and_clauses: list[dict[str, Any]] = [identity_clause]
    if not no_esg_filter:
        and_clauses.append({"$or": [{"keyword": kw} for kw in esg_terms]})
    and_clauses.append({"lang": "eng"})
    complex_query: dict[str, Any] = {
        "$query": {
            "$and": and_clauses,
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

    return _post_and_parse_newsapi(body, company, log_keyword=keyword, since=date_start)


def _post_and_parse_newsapi(
    body: dict, company: Company, *, log_keyword: str, since: str,
    source_type: str = "newsapi_ai",
) -> list[dict]:
    """POST a NewsAPI.ai getArticles body, parse results into article dicts,
    record the budget spend, and log. Shared by the company-named query
    (fetch_newsapi_ai_for_company) and the industry/thematic query
    (fetch_industry_thematic_for_company). ``source_type`` tags each article's
    lane so the orchestrator can apply lane-conditional relevance guards."""
    try:
        resp = _SESSION.post(NEWSAPI_AI_URL, json=body, timeout=25)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("NewsAPI.ai fetch failed for %s (%s): %s", company.slug, log_keyword, exc)
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
            "source_type": source_type,
            "metadata": {
                "sentiment": item.get("sentiment"),
                "source_type": source_type,
                "image_url": item.get("image") or "",
                "concepts": [
                    (c.get("label") or {}).get("eng", "")
                    for c in (item.get("concepts") or [])[:5]
                ],
            },
        })

    logger.info(
        "NewsAPI.ai [%s]: %d articles for %s (query=%r, since=%s, avg %d chars)",
        source_type, len(articles), company.slug, log_keyword, since,
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
# Phase 53 (B) — INDUSTRY / THEMATIC ESG lane
# ---------------------------------------------------------------------------
# Most companies have no company-NAMED ESG event in a 30-day window (proven: a
# bank's only company-headlined news is macro/market). Their material ESG news
# is SECTOR / REGULATORY / THEMATIC — where the company is NOT named but the news
# is material via its industry + ESG exposures. This lane fetches that, keyed on
# the company's INDUSTRY + its SASB material topics (Phase A2), with NO
# company-identity clause. Search-hint vocab (kept lean for EventRegistry's
# 80-word/plan keyword limit); the SASB material weights + the Stage-4 relevance
# scorer do the precision downstream.

# Industry (resolver canonical label) → lean SECTOR scoping terms. Keeps the
# thematic query inside the company's sector (regulator names are the sharpest
# India scoping anchors).
# Headline-friendly so the title-locked thematic query matches genuine
# sector-ESG headlines (e.g. "RBI tightens climate norms for banks").
_INDUSTRY_SECTOR_TERMS: dict[str, tuple[str, ...]] = {
    "Financials/Banking": ("banks", "lenders", "RBI", "NBFC"),
    "Asset Management": ("mutual funds", "AMCs", "SEBI"),
    "Insurance": ("insurers", "IRDAI"),
    "Power/Energy": ("power", "thermal", "discoms", "utilities"),
    "Renewable Energy": ("solar", "renewable", "wind"),
    "Oil & Gas": ("refiners", "oil", "gas"),
    "Steel": ("steel",),
    "Metals & Mining": ("mining", "metals", "smelter"),
    "Automotive": ("automakers", "carmakers", "EVs"),
    "Information Technology": ("IT firms", "software", "tech"),
    "Pharmaceuticals": ("pharma", "drugmakers"),
    "Chemicals": ("chemicals", "petrochemical"),
    "Consumer/Beverage": ("beverages", "FMCG"),
    "FMCG": ("FMCG", "packaged foods"),
    "Footwear & Accessories": ("footwear", "apparel"),
    "Apparel Manufacturing": ("apparel", "textile"),
    "Luxury Goods": ("luxury", "apparel"),
    "Household & Personal Products": ("consumer goods", "personal care"),
    "Industrials/Conglomerate": ("industrials", "conglomerate"),
    "Telecommunications": ("telecom", "telcos", "TRAI"),
    "Real Estate": ("real estate", "realty", "developers"),
    "Aerospace & Defense": ("defence", "aerospace"),
}

# SASB topic suffix (Phase A2) → lean ESG-EVENT search phrases. Harm/enforcement
# weighted so the lane catches substantive ESG developments, not generic PR.
_TOPIC_SEARCH_TERMS: dict[str, tuple[str, ...]] = {
    "climate": ("climate risk", "climate disclosure"),
    "climate_adaptation": ("climate adaptation", "physical climate risk"),
    # Phase 53.G — add India hard-ESG event vocabulary so the thematic lane
    # surfaces SUBSTANTIVE heavy-industry ESG news (NGT/CPCB orders, coal/fly-ash,
    # emission-norm enforcement) instead of soft "net zero / renewable" PR. These
    # are the headlines that actually generate critical-grade power/metals events.
    "emissions": ("carbon emissions", "emission norms", "coal", "fly ash"),
    "energy": ("energy transition", "renewable energy"),
    "water": ("water pollution", "water scarcity"),
    "pollution": ("air pollution", "NGT", "CPCB", "environmental clearance"),
    "waste": ("hazardous waste", "plastic waste"),
    "biodiversity": ("deforestation", "biodiversity"),
    "health_safety": ("workplace safety", "industrial accident"),
    "supply_chain_labor": ("human rights", "forced labour"),
    "human_capital": ("layoffs", "labour dispute"),
    "dei": ("workplace diversity",),
    "community": ("land acquisition", "displacement"),
    "data_privacy": ("data breach", "data privacy"),
    "product_safety": ("product recall", "product safety"),
    "stakeholder_governance": ("corporate governance", "shareholder dispute"),
    "board_leadership": ("board shakeup", "executive resignation"),
    "ethics_compliance": ("regulatory penalty", "fraud", "show cause"),
    "transparency": ("ESG disclosure", "BRSR"),
    "tax_transparency": ("tax evasion",),
    "risk_management": ("risk management failure",),
}

_THEMATIC_LANE_CAP = 8  # max thematic candidates so company-named events dominate

# Region → EventRegistry source-location URI. Scopes the thematic lane to the
# company's home market (an India bank wants Indian banking-ESG news, not a
# Missouri coal-plant story). Multi-country regions (EU/APAC/GLOBAL) are left
# unscoped — the sector terms + materiality scorer carry it there.
_REGION_LOCATION_URI: dict[str, str] = {
    "INDIA": "http://en.wikipedia.org/wiki/India",
    "UK": "http://en.wikipedia.org/wiki/United_Kingdom",
    "US": "http://en.wikipedia.org/wiki/United_States",
}


def _sasb_sector_for(company: Company) -> str:
    """Resolve the company's SASB sector label (for the material-topic lookup).

    Falls back to the industry→SASB map whenever the stored sasb_category is
    missing/placeholder (e.g. SBI carries the literal "Unknown" in the DB) so the
    thematic lane never silently degrades to the generic-ESG fallback vocab."""
    cat = (getattr(company, "sasb_category", "") or "").strip()
    if cat and cat.lower() not in ("other / general", "other", "unknown", "n/a", "none", ""):
        return cat
    try:
        from engine.ingestion.llm_company_resolver import INDUSTRY_TO_SASB_DEFAULT
        return INDUSTRY_TO_SASB_DEFAULT.get((getattr(company, "industry", "") or "").strip(), "")
    except Exception:  # noqa: BLE001
        return ""


def _build_thematic_terms(company: Company) -> tuple[list[str], list[str]]:
    """(sector_terms, esg_terms) for the industry/thematic query. ESG terms come
    from the company's top SASB material topics (Phase A2) → search phrases."""
    import re as _re
    industry = (getattr(company, "industry", "") or "").strip()
    sector_terms = list(_INDUSTRY_SECTOR_TERMS.get(industry, ()))
    if not sector_terms:
        sector_terms = [t for t in _re.split(r"[/\s&,-]+", industry) if len(t) > 3][:3]
    esg_terms: list[str] = []
    seen: set[str] = set()
    try:
        from engine.ontology.sasb_loader import query_material_topics_for_sector
        for suffix, _w, _kind in query_material_topics_for_sector(_sasb_sector_for(company))[:6]:
            for term in _TOPIC_SEARCH_TERMS.get(suffix, ()):
                if term not in seen:
                    seen.add(term)
                    esg_terms.append(term)
    except Exception:  # noqa: BLE001
        pass
    if not esg_terms:
        esg_terms = ["ESG", "sustainability", "regulatory penalty", "emissions"]
    # Cap well under EventRegistry's 80-word/plan keyword limit.
    return sector_terms[:6], esg_terms[:14]


def _thematic_fetch_enabled(company: Company) -> bool:
    """Per-tenant primitive_calibration.industry_thematic_fetch ∈ {on|off|auto};
    auto is budget-gated (fail-open)."""
    cal = getattr(company, "primitive_calibration", None) or {}
    mode = str(cal.get("industry_thematic_fetch", "auto")).strip().lower()
    if mode == "off":
        return False
    if mode == "on":
        return True
    try:
        from engine.ingestion.news_router import get_router
        floor = int(os.environ.get("SNOWKAP_THEMATIC_FETCH_MIN_REMAINING", "400"))
        return get_router().budget.remaining() > floor
    except Exception:  # noqa: BLE001
        return True


def fetch_industry_thematic_for_company(
    company: Company, max_results: int = _THEMATIC_LANE_CAP, freshness_days: int = 30,
) -> list[dict]:
    """ONE NewsAPI.ai call for INDUSTRY/THEMATIC ESG news — (sector terms) AND
    (the company's SASB material-topic ESG terms), NO company-identity clause,
    last `freshness_days`. Returns articles tagged source_type='industry_thematic'
    so the orchestrator bypasses the company-name guard for them."""
    api_key = (
        os.environ.get("NEWSAPI_AI_KEY")
        or os.environ.get("NEWSAPI_AI_API_KEY")
        or os.environ.get("EVENT_REGISTRY_API_KEY")
        or ""
    )
    if not api_key:
        return []
    sector_terms, esg_terms = _build_thematic_terms(company)
    if not sector_terms or not esg_terms:
        return []
    date_start = (datetime.now(timezone.utc).date() - timedelta(days=freshness_days)).isoformat()
    date_end = datetime.now(timezone.utc).date().isoformat()
    # Both a SECTOR term AND an ESG term must be in the TITLE — a genuine
    # sector-ESG headline ("RBI tightens climate norms for banks"), not an
    # article incidentally mentioning a lender + a fraud somewhere in the body
    # (the noise that sank the earlier title-lock-dropped approach).
    and_clauses: list[dict[str, Any]] = [
        {"$or": [{"keyword": t, "keywordLoc": "title"} for t in sector_terms]},
        {"$or": [{"keyword": t, "keywordLoc": "title"} for t in esg_terms]},
        {"lang": "eng"},
    ]
    region = (getattr(company, "framework_region", "") or "").strip().upper()
    loc_uri = _REGION_LOCATION_URI.get(region)
    if loc_uri:
        and_clauses.append({"sourceLocationUri": loc_uri})
    body = {
        "action": "getArticles",
        "query": {
            "$query": {
                "$and": and_clauses,
                "dateStart": date_start,
                "dateEnd": date_end,
            },
        },
        "resultType": "articles",
        "articlesPage": 1,
        "articlesCount": min(max_results, 50),
        "articlesSortBy": "date",
        "includeArticleBody": True,
        "articleBodyLen": -1,
        "includeArticleImage": True,
        "apiKey": api_key,
    }
    return _post_and_parse_newsapi(
        body, company,
        log_keyword=f"thematic:{(getattr(company, 'industry', '') or '').strip()}",
        since=date_start, source_type="industry_thematic",
    )


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
    # Phase 49.3 — index / m-cap / commodity market recaps that name the company
    # only as one of many (surfaced when the title-lock is dropped for the banks)
    "stock market recap", "market recap", "mcap of", "m-cap of",
    "valued firms", "lakh crore in value", "crore in value", "erodes by",
    "biggest gainers", "biggest losers", "top-10 firms", "top 10 firms",
    "top-10 valued", "gold price", "sensex", "nifty", "market cap of",
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
    # Phase 50 — "X vs Y: Which Renewable Energy Stock Can ..." stock-comparison
    # roundups (the "Suzlon vs JSW" card) + "should you buy/sell" investment-
    # advice framings. The earlier "stock offers better" marker missed the
    # "which ... stock can" variant.
    if re.search(r"\bwhich\b[^.]{0,40}\bstock", title_lower):
        return True
    if (" vs " in title_lower or " vs. " in title_lower) and (
        "stock" in title_lower or "share" in title_lower
    ):
        return True
    if re.search(r"\bshould\s+(?:you|i|investors?)\b.*\b(buy|sell|invest|book)\b", title_lower):
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
    # Phase 49.3 — also accept seeded `news_aliases` (e.g. a boutique AMC + its
    # founder: "Madhusudan Kela" IS Singularity AMC's fund). Lets genuine
    # founder/fund-activity articles attach to a tenant whose legal name rarely
    # appears in headlines, without loosening the guard for tenants that have
    # no aliases. The roundup guard still drops the "N stocks" list articles.
    cal = getattr(company, "primitive_calibration", None) or {}
    for alias in (cal.get("news_aliases") or []):
        if isinstance(alias, str) and alias.strip():
            variants.extend(_company_name_variants(alias))
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

    # Phase 53 (B) — INDUSTRY/THEMATIC ESG lane. Company-named ESG events are
    # scarce; sector/regulatory/thematic ESG news (company NOT named) is the
    # always-available baseline. Fetched on the company's industry + SASB
    # material topics, tagged source_type='industry_thematic', capped + budget-
    # gated, and exempted from the company-name guard below (the whole point is
    # the company is not named — relevance is via INDUSTRY materiality downstream).
    if _thematic_fetch_enabled(company):
        for art in fetch_industry_thematic_for_company(
            company, max_results=_THEMATIC_LANE_CAP, freshness_days=freshness_days,
        ):
            url = art.get("url") or ""
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            art["query"] = f"thematic:{(getattr(company, 'industry', '') or '').strip()}"
            raw_articles.append(art)

    # Phase 52 — ESG-aware SECOND fetch (complements the Phase 53 thematic lane).
    # The primary query title-locks on the company name, so market-dominated names
    # (power/renewable) get only stock/growth coverage. This second query drops
    # the title-lock (company in title OR BODY) and swaps in the curated
    # ESG-MATERIAL / harm vocab, so a substantive ESG/negative story that mentions
    # the company in the BODY (e.g. "new emission norms ... incl. Adani's Mundra")
    # enters the feed and can score critical. Where the thematic lane catches
    # company-NOT-named sector news, this catches company-in-body news. Budget-
    # gated; both run through the SAME dedup + relevance gauntlet below (no double
    # processing), each fetch records its own spend (no double-charge). A
    # per-tenant `esg_material_keywords` calibration list can override the vocab.
    if _esg_second_fetch_enabled(company):
        _cal = getattr(company, "primitive_calibration", None) or {}
        _override = _cal.get("esg_material_keywords")
        # Phase 56.C — compose base ∪ sector ∪ jurisdiction ∪ override (the
        # override ADDS to the base, never replaces). Keyed off _sasb_sector_for
        # so an "Unknown" sasb_category still resolves to the real sector overlay.
        esg_vocab = _compose_esg_material(company, _override)
        for art in fetch_newsapi_ai_for_company(
            company, max_results=limit, freshness_days=freshness_days,
            strict_title=False, esg_keywords=esg_vocab,
        ):
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
        # Phase 53 (B) — the thematic lane's articles are ABOUT the sector and
        # legitimately name many peers + a regulator, so the company-name guard
        # and the multi-org wrap-up guard would (correctly, for the company lane)
        # drop them. Exempt the thematic lane from BOTH; keep calendar + freshness
        # + semantic-dedup on both lanes, and rely on the Stage-4 industry
        # materiality scorer + the Phase-C cross-entity gate for precision.
        is_thematic = raw.get("source_type") == "industry_thematic"
        if not is_thematic and _is_wrapup_article(raw.get("title") or "", raw.get("content") or "", company):
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
        if not is_thematic and not _is_article_about_company(raw.get("title") or "", raw.get("content") or "", company):
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
