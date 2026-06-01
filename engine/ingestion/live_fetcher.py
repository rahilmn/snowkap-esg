"""Phase 31 — Live-fetch hybrid news ingestion.

Fetches Google News RSS on demand using the two LLM-crafted queries
stored on the ``companies`` row (``sustainability_query`` +
``general_query``). Returns a flat list of bare articles — no LLM
enrichment, no ontology pipeline, no SQLite write — so a HomePage load
can render fresh headlines in ≤ 1 second instead of waiting for the
60-min auto-scheduler to run.

The "hybrid" piece: every article in the response carries an
``is_analyzed`` flag. When ``True`` the on-disk insight payload exists
(role explainer + criticality summary stamped at ingest time) and the
frontend renders the full Phase 28/29 view. When ``False`` the article
is a live headline only; clicking it triggers
``api/routes/legacy_adapter::news_trigger_analysis`` to enrich on
demand (~60s LLM call).

Design choices:
- One short cache (60s) per (slug, query_kind) so a /home auto-refresh
  doesn't hammer Google News.
- Fail-soft: any single query that errors returns []; the response
  still ships whatever the other query produced.
- We do NOT dedupe against ``article_index`` here — the response
  marker ``is_analyzed`` is enough for the frontend, and the live
  fetcher must stay fast.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from engine.ingestion.news_fetcher import fetch_newsapi_ai
from engine.models import companies_store
from engine.index import sqlite_index

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-process cache — (slug, kind) → (timestamp, articles)
# ---------------------------------------------------------------------------

_CACHE_TTL_S = 60.0
_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}

# Phase 31 — 30-day date filter on the live feed. Matches the [DATE_FILTER]
# stage in the described hybrid pipeline: anything older than 30 days is
# stripped before merge so the home page never surfaces stale headlines.
_LIVE_MAX_AGE_DAYS = 30


def _cache_get(slug: str, kind: str) -> list[dict[str, Any]] | None:
    entry = _cache.get((slug, kind))
    if entry is None:
        return None
    ts, payload = entry
    if (time.monotonic() - ts) > _CACHE_TTL_S:
        _cache.pop((slug, kind), None)
        return None
    return payload


def _cache_put(slug: str, kind: str, payload: list[dict[str, Any]]) -> None:
    _cache[(slug, kind)] = (time.monotonic(), payload)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class LiveArticle:
    """One article from the live fetch. Mirrors the minimal shape the
    HomePage feed cards consume — no deep_insight here on purpose."""
    id: str
    title: str
    url: str
    source: str
    published_at: str | None
    summary: str
    image_url: str
    company_slug: str
    kind: str  # "sustainability" | "general"
    is_analyzed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published_at": self.published_at,
            "summary": self.summary,
            "image_url": self.image_url,
            "company_slug": self.company_slug,
            "kind": self.kind,
            "is_analyzed": self.is_analyzed,
        }


def _article_id(url: str) -> str:
    """16-hex-char ID matching the existing ``article_index.id`` shape,
    so a live article whose URL was previously analysed gets matched
    against the on-disk insight without a separate lookup table."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _is_within_window(published_at: str | None, *, days: int = _LIVE_MAX_AGE_DAYS) -> bool:
    """Return True when ``published_at`` is within the last ``days``.

    Empty / unparseable timestamps return True — better to surface an
    article whose date Google News couldn't supply than to silently
    drop fresh news with a malformed pubdate header.
    """
    if not published_at:
        return True
    try:
        # Most RSS feeds emit ISO-8601 already (news_fetcher._parse_published).
        # Accept both "2026-05-18T10:00:00+00:00" and bare "2026-05-18".
        if "T" in published_at:
            ts = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        else:
            ts = datetime.fromisoformat(published_at).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return ts >= cutoff


def _run_query(
    query: str,
    *,
    company_slug: str,
    kind: str,
    country: str | None,
    limit: int,
    analyzed_ids: set[str],
) -> list[LiveArticle]:
    """One query → Google News → LiveArticle list. Cached per
    (slug, kind) for 60s. Returns [] on any error."""
    if not query or not query.strip():
        return []

    cached = _cache_get(company_slug, kind)
    if cached is not None:
        rows = cached
    else:
        try:
            # Phase 48.A — Google News removed; live search uses NewsAPI.ai.
            rows = fetch_newsapi_ai(query, max_results=max(limit, 10))
        except Exception as exc:  # noqa: BLE001 — live path must not crash the response
            logger.warning(
                "live_fetcher: NewsAPI.ai failed for %s/%s: %s",
                company_slug, kind, exc,
            )
            rows = []
        _cache_put(company_slug, kind, rows)

    out: list[LiveArticle] = []
    seen_urls: set[str] = set()
    dropped_stale = 0
    for r in rows:
        url = (r.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        published_at = r.get("published_at")
        # [DATE_FILTER] — 30-day cutoff per the described hybrid spec.
        if not _is_within_window(published_at):
            dropped_stale += 1
            continue
        aid = _article_id(url)
        meta = r.get("metadata") or {}
        out.append(
            LiveArticle(
                id=aid,
                title=(r.get("title") or "").strip(),
                url=url,
                source=(r.get("source") or "").strip() or "Google News",
                published_at=published_at,
                summary=(r.get("summary") or "").strip()[:400],
                image_url=(meta.get("image_url") or "").strip(),
                company_slug=company_slug,
                kind=kind,
                is_analyzed=aid in analyzed_ids,
            )
        )
        if len(out) >= limit:
            break
    if dropped_stale:
        logger.info(
            "live_fetcher: dropped %d stale articles (>30d) for %s/%s",
            dropped_stale, company_slug, kind,
        )
    return out


def _analyzed_ids_for(slug: str) -> set[str]:
    """Pull every indexed article-id for this slug so the live response
    can tag headlines we've already analysed. ``query_feed`` honours the
    14-day freshness window, which is what we want — older insights
    shouldn't shadow a fresh live result."""
    try:
        rows = sqlite_index.query_feed(
            company_slug=slug,
            tier=None,
            limit=200,
            offset=0,
        )
        return {r.get("id") for r in rows if r.get("id")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("live_fetcher: analyzed-id lookup failed for %s: %s", slug, exc)
        return set()


@dataclass
class LiveFetchResult:
    company_slug: str
    sustainability: list[LiveArticle] = field(default_factory=list)
    general: list[LiveArticle] = field(default_factory=list)
    queries_used: dict[str, str] = field(default_factory=dict)
    cached: bool = False

    def merged(self, limit: int = 10) -> list[LiveArticle]:
        """Sustainability first (the user's stated priority), then
        general — deduped by article id."""
        seen: set[str] = set()
        out: list[LiveArticle] = []
        for a in self.sustainability + self.general:
            if a.id in seen:
                continue
            seen.add(a.id)
            out.append(a)
            if len(out) >= limit:
                break
        return out

    def to_dict(self, limit: int = 10) -> dict[str, Any]:
        merged = self.merged(limit=limit)
        return {
            "company_slug": self.company_slug,
            "items": [a.to_dict() for a in merged],
            "count": len(merged),
            "sustainability_count": sum(1 for a in merged if a.kind == "sustainability"),
            "general_count": sum(1 for a in merged if a.kind == "general"),
            "queries_used": self.queries_used,
            "cached": self.cached,
        }


def _ensure_company_enriched(record: Any) -> Any:
    """Phase 31 — lazy safety net. If a company row landed in the table
    without LLM-crafted queries (legacy baselines, migration applied
    after seed, partially-failed onboard), generate them on first use
    and persist back. The next live fetch reads the freshly-stamped row
    so we only pay the LLM cost once.

    Returns the (possibly updated) record. Fails open — on any LLM
    error we return the row untouched and the caller falls back to the
    deterministic string templates.
    """
    needs_sustainability = not (getattr(record, "sustainability_query", None) or "").strip()
    needs_general = not (getattr(record, "general_query", None) or "").strip()
    if not (needs_sustainability or needs_general):
        return record
    try:
        from engine.ingestion.llm_query_generator import generate_queries
        llm = generate_queries(
            record.name,
            industry=record.industry,
            region=record.framework_region,
        )
        companies_store.upsert(
            slug=record.slug,
            name=record.name,
            domain=record.domain,
            industry=record.industry,
            market_cap_tier=record.market_cap_tier,
            yfinance_ticker=record.yfinance_ticker,
            eodhd_ticker=record.eodhd_ticker,
            framework_region=record.framework_region,
            revenue_cr=record.revenue_cr,
            status=record.status,
            sustainability_query=llm.sustainability_query,
            general_query=llm.general_query,
        )
        logger.info(
            "live_fetcher: lazily enriched queries for slug=%s",
            record.slug,
        )
        return companies_store.get(record.slug) or record
    except Exception as exc:  # noqa: BLE001 — safety net must never break the live path
        logger.warning(
            "live_fetcher: lazy enrichment failed for %s: %s — using deterministic fallback",
            record.slug, exc,
        )
        return record


def fetch_live_for_company(
    slug: str,
    *,
    limit: int = 10,
) -> LiveFetchResult:
    """Run both LLM-crafted queries (sustainability + general) and
    return a merged feed. The 3 'critical' analyzed articles surface
    naturally because their ``is_analyzed=True`` flag carries through;
    the frontend can sort them above unanalysed headlines.

    Returns an empty result with empty `queries_used` if the company is
    not in the companies table.
    """
    slug = (slug or "").strip().lower()
    if not slug:
        return LiveFetchResult(company_slug="")

    record = companies_store.get(slug)
    if record is None:
        logger.info("live_fetcher: no companies row for slug=%s", slug)
        return LiveFetchResult(company_slug=slug)

    # Lazy enrichment — legacy baseline rows landed without LLM-crafted
    # queries because they predate Phase 31. Generate + persist on first
    # call so the second call reads pre-baked values.
    record = _ensure_company_enriched(record)

    country = None
    try:
        from engine.config import get_company
        co = get_company(slug)
        country = getattr(co, "headquarter_country", None)
    except Exception:  # noqa: BLE001 — country is optional
        country = None

    sustainability_q = (getattr(record, "sustainability_query", None) or "").strip()
    general_q = (getattr(record, "general_query", None) or "").strip()

    # Final deterministic fallback if even the LLM call failed
    if not sustainability_q:
        sustainability_q = f"{record.name} ESG sustainability climate"
    if not general_q:
        general_q = f"{record.name} earnings results regulatory"

    analyzed_ids = _analyzed_ids_for(slug)

    # Split the limit roughly 60/40 in favour of sustainability so the
    # ESG-first promise of the platform actually shows up.
    sustain_limit = max(1, (limit * 6) // 10)
    general_limit = max(1, limit - sustain_limit)

    sustain = _run_query(
        sustainability_q,
        company_slug=slug,
        kind="sustainability",
        country=country,
        limit=sustain_limit,
        analyzed_ids=analyzed_ids,
    )
    general = _run_query(
        general_q,
        company_slug=slug,
        kind="general",
        country=country,
        limit=general_limit,
        analyzed_ids=analyzed_ids,
    )

    return LiveFetchResult(
        company_slug=slug,
        sustainability=sustain,
        general=general,
        queries_used={
            "sustainability": sustainability_q,
            "general": general_q,
        },
        cached=any(
            _cache_get(slug, k) is not None
            for k in ("sustainability", "general")
        ),
    )
