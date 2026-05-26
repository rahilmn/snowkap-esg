"""Phase 35 — Full-text article extractor.

Closes the catastrophic body-capture gap audited on 2026-05-24: 0 / 50 stored
insights across 8 companies had any article body, because Google News RSS
returns ~140-char title-duplicate "content" and was the default fetcher.
The Stage 10 LLM was generating ₹ figures + recommendations from headline-only
input — every number tagged "(engine estimate)" because there was no source
text to ground on.

This module is the secondary fallback after NewsAPI.ai. When NewsAPI.ai
misses an article (or it was previously fetched via Google News RSS),
the resolver here:

  1. If the URL is a Google News redirect blob (CBMi...), follow it to the
     real publisher URL via a HEAD/GET redirect chain.
  2. GET the publisher URL with a browser-like User-Agent.
  3. Extract the main article body via `trafilatura.extract` (preferred)
     or BeautifulSoup as a fallback when trafilatura returns None.
  4. Cache the result in `data/snowkap.db::article_full_text` so a re-ingest
     doesn't re-scrape — keyed on URL hash, with a 7-day TTL.

Public surface:
  - `resolve_publisher_url(url) -> str`   (follows Google News redirect)
  - `extract_full_text(url) -> ExtractResult | None`
  - `backfill_input_file(path) -> bool`   (mutates in place if body added)

Defensive: every external call is wrapped — network failure NEVER blocks
the pipeline. Returns None / leaves the file untouched.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Browser-like UA so publishers don't 403 us as a bot. A real Chrome string;
# many news sites block "python-requests/2.32.5" out of the box.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_REQUEST_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
}

# Lightweight markers that suggest the body we extracted is a paywall stub
# rather than real article text. When body < 500 chars AND contains any
# marker, drop it — feeding "Subscribe now to read more" to the LLM as
# article content is worse than feeding nothing.
_PAYWALL_MARKERS = (
    "subscribe to read",
    "subscribe to continue",
    "sign in to read",
    "this article is for subscribers",
    "for full access",
    "premium subscribers only",
    "free trial",
    "create a free account",
    "you have reached",
    "free articles remaining",
)

_MIN_BODY_CHARS = 300  # below this we treat the extraction as failed


@dataclass
class ExtractResult:
    """Result of a successful full-text extraction."""
    body: str
    title: str
    publisher_url: str
    extracted_at: float
    char_count: int

    def as_dict(self) -> dict:
        return {
            "body": self.body,
            "title": self.title,
            "publisher_url": self.publisher_url,
            "extracted_at": self.extracted_at,
            "char_count": self.char_count,
        }


# ──────────────────────────────────────────────────────────────────────────
# Cache layer (Supabase Postgres in prod / SQLite in dev via engine.db dispatcher)
# ──────────────────────────────────────────────────────────────────────────
#
# Phase 36 fix — switched from raw `sqlite3.connect(_cache_path())` to the
# `engine.db.connect()` dispatcher so the cache table lives in the same
# backend as the rest of the corpus (Supabase Postgres in prod via
# SNOWKAP_DB_BACKEND=postgres, SQLite in dev). Critical for multi-worker
# deploys — otherwise each uvicorn worker would have its own SQLite cache
# file and a body scraped by worker A would be invisible to worker B.

_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS article_full_text (
    url_hash       TEXT PRIMARY KEY,
    original_url   TEXT NOT NULL,
    publisher_url  TEXT,
    body           TEXT,
    title          TEXT,
    char_count     INTEGER,
    extracted_at   REAL NOT NULL,
    status         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_article_full_text_status
    ON article_full_text(status, extracted_at);
"""

_CACHE_TTL_SECONDS = 7 * 24 * 3600          # 7 days for successful extractions
_CACHE_FAILURE_TTL_SECONDS = 6 * 3600       # 6 hours for failures (let the cron retry quickly)
_SCHEMA_READY = False


def _ensure_cache_schema() -> None:
    """Bootstrap the cache table on the active backend (SQLite or Supabase)."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    from engine.db import connect as _db_connect
    with _db_connect() as conn:
        conn.executescript(_CACHE_SCHEMA)
    _SCHEMA_READY = True


def _normalize_url_for_hash(url: str) -> str:
    """Strip Google News tracking suffixes (``?oc=5``, ``?gclid=...``)
    so the same article isn't cached under multiple keys.

    Without this, a Google News URL fetched at different times with
    different ``oc`` values (Google's locale-specific routing) produces
    cache misses even though the underlying CBMi blob is identical.
    """
    if not url:
        return ""
    # For Google News URLs, drop everything from `?` onward — the CBMi
    # blob in the path is the only identifier that matters.
    if "news.google.com" in url and "?" in url:
        return url.split("?", 1)[0]
    return url


def _url_hash(url: str) -> str:
    return hashlib.sha256(_normalize_url_for_hash(url).encode("utf-8")).hexdigest()[:32]


def _cache_get(url: str) -> Optional[dict]:
    """Return cached row when fresh, else None.

    Uses the engine.db dispatcher so it reads from Supabase Postgres in
    production (SNOWKAP_DB_BACKEND=postgres) and SQLite in dev. The query
    SQL is the common subset (parameterised SELECT) supported by both.
    """
    try:
        _ensure_cache_schema()
        from engine.db import connect as _db_connect
        with _db_connect() as conn:
            row = conn.execute(
                "SELECT url_hash, original_url, publisher_url, body, title, "
                "char_count, extracted_at, status FROM article_full_text "
                "WHERE url_hash = ?",
                (_url_hash(url),),
            ).fetchone()
        if not row:
            return None
        # The dispatcher returns a Row-like object that supports both
        # index + key access; normalise to a plain dict for caller use.
        if hasattr(row, "keys"):
            d = {k: row[k] for k in row.keys()}
        else:
            d = {
                "url_hash": row[0], "original_url": row[1],
                "publisher_url": row[2], "body": row[3], "title": row[4],
                "char_count": row[5], "extracted_at": row[6], "status": row[7],
            }
        # Phase 36 — failures expire faster (6h) than successes (7d) so
        # the periodic retry cron can pick off paywalled/blocked URLs
        # without waiting a full week for the cached failure to expire.
        age = time.time() - float(d["extracted_at"])
        status = d["status"]
        ttl = (
            _CACHE_TTL_SECONDS if status == "ok" else _CACHE_FAILURE_TTL_SECONDS
        )
        if age > ttl:
            return None
        return d
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        logger.warning("full_text_extractor cache GET failed: %s", exc)
        return None


def _cache_put(url: str, *, status: str, result: Optional[ExtractResult]) -> None:
    """Persist a cache entry. Uses ON CONFLICT upsert so the same SQL
    runs on both SQLite and Postgres without the dialect translator
    having to rewrite `INSERT OR REPLACE`.
    """
    try:
        _ensure_cache_schema()
        from engine.db import connect as _db_connect
        with _db_connect() as conn:
            conn.execute(
                """
                INSERT INTO article_full_text
                  (url_hash, original_url, publisher_url, body, title,
                   char_count, extracted_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url_hash) DO UPDATE SET
                  original_url = excluded.original_url,
                  publisher_url = excluded.publisher_url,
                  body = excluded.body,
                  title = excluded.title,
                  char_count = excluded.char_count,
                  extracted_at = excluded.extracted_at,
                  status = excluded.status
                """,
                (
                    _url_hash(url),
                    url,
                    result.publisher_url if result else None,
                    result.body if result else None,
                    result.title if result else None,
                    result.char_count if result else 0,
                    time.time(),
                    status,
                ),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("full_text_extractor cache PUT failed: %s", exc)


# ──────────────────────────────────────────────────────────────────────────
# Resolver — Google News redirect → publisher URL
# ──────────────────────────────────────────────────────────────────────────


_GOOGLE_NEWS_HOST_RE = re.compile(r"^https?://news\.google\.com/", re.IGNORECASE)


def is_google_news_redirect(url: str) -> bool:
    """True when the URL is a Google News redirect blob (CBMi..., etc)."""
    return bool(_GOOGLE_NEWS_HOST_RE.match(url or ""))


def resolve_publisher_url(url: str, *, timeout: float = 10.0) -> str:
    """Follow redirects to the actual publisher URL.

    For Google News blob URLs (``https://news.google.com/rss/articles/CBMi...``),
    Google serves an HTML interstitial that requires session cookies to
    navigate. A naive `requests.get(...).url` returns the interstitial
    page, not the publisher.

    Phase 35.5 — pivoted to ``googlenewsdecoder.gnewsdecoder()`` which
    decodes the CBMi blob via Google's documented batchexecute RPC.
    Live verified on the YES Bank pledge URL:
        ``https://news.google.com/rss/articles/CBMikwJB...``
        → ``https://scanx.trade/stock-market-news/companies/...``
    Returns the real publisher URL in ~1-2s.

    Falls back to the legacy HTTP-follow + interstitial-scrape approach
    when the decoder errors (e.g. Google rotated their RPC schema or our
    network is offline). When everything fails, returns the original URL
    and the caller drops the article via the headline-only cap.
    """
    if not is_google_news_redirect(url):
        return url

    # Primary path — googlenewsdecoder. Handles the batchexecute RPC dance
    # internally + caches its session signature so repeat calls are cheap.
    try:
        from googlenewsdecoder import gnewsdecoder
        result = gnewsdecoder(url, interval=1)
        if isinstance(result, dict) and result.get("status"):
            decoded = result.get("decoded_url") or ""
            if decoded and not _GOOGLE_NEWS_HOST_RE.match(decoded):
                return decoded
    except Exception as exc:  # noqa: BLE001 — fall through to legacy path
        logger.debug(
            "resolve_publisher_url: googlenewsdecoder failed (%s) — "
            "falling back to legacy interstitial scrape",
            type(exc).__name__,
        )

    # Legacy fallback — HTTP follow + interstitial scrape. Rarely succeeds
    # for Google News URLs but kept as a safety net for non-Google
    # redirect URLs that happen to share the function path.
    try:
        resp = requests.get(
            url, headers=_REQUEST_HEADERS, timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        logger.warning("resolve_publisher_url: GET failed %s: %s", url[:80], exc)
        return url

    if resp.url and not _GOOGLE_NEWS_HOST_RE.match(resp.url):
        return resp.url

    html = resp.text or ""
    candidates: list[str] = []
    for m in re.finditer(
        r'(?:href|data-n-au|url=)\s*=?\s*"(https?://[^"]+)"',
        html, flags=re.IGNORECASE,
    ):
        cand = m.group(1)
        if _GOOGLE_NEWS_HOST_RE.match(cand):
            continue
        if "google.com" in cand or "gstatic.com" in cand or "googleapis.com" in cand:
            continue
        candidates.append(cand)
    if candidates:
        candidates.sort(key=lambda u: len(u), reverse=True)
        return candidates[0]
    return url


# ──────────────────────────────────────────────────────────────────────────
# Extractor — publisher URL → article body
# ──────────────────────────────────────────────────────────────────────────


def _is_paywall_stub(body: str) -> bool:
    if not body:
        return False
    if len(body) >= 1500:
        return False
    low = body.lower()
    return any(marker in low for marker in _PAYWALL_MARKERS)


def _extract_with_trafilatura(html: str, url: str) -> Optional[tuple[str, str]]:
    """Return (title, body) or None when extraction fails."""
    try:
        import trafilatura
    except ImportError:
        return None
    body = trafilatura.extract(
        html, url=url,
        favor_recall=True,           # prefer more content over precision
        include_comments=False,
        include_tables=False,
        no_fallback=False,
    )
    if not body:
        return None
    # Title from trafilatura metadata
    title = ""
    try:
        meta = trafilatura.extract_metadata(html, default_url=url)
        if meta and meta.title:
            title = meta.title
    except Exception:  # noqa: BLE001
        pass
    return (title, body.strip())


def _extract_with_bs4(html: str) -> Optional[tuple[str, str]]:
    """Crude fallback: concatenate all <p> elements under <article> or <main>."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # noqa: BLE001
        return None
    title_el = soup.find("title")
    title = title_el.get_text(strip=True) if title_el else ""
    # Prefer <article> > <main> > body
    container = soup.find("article") or soup.find("main") or soup.body
    if container is None:
        return None
    paras = [p.get_text(" ", strip=True) for p in container.find_all("p")]
    body = "\n\n".join(p for p in paras if len(p) > 30)
    if not body or len(body) < _MIN_BODY_CHARS:
        return None
    return (title, body)


def extract_full_text(
    url: str, *, timeout: float = 12.0, use_cache: bool = True,
) -> Optional[ExtractResult]:
    """Resolve the URL + fetch the publisher page + extract main content.

    Returns None when:
      - The URL can't be resolved
      - The publisher returns 4xx / 5xx
      - The extracted body is below _MIN_BODY_CHARS (300)
      - The extracted body looks like a paywall stub
      - trafilatura + BeautifulSoup both fail

    Caches every outcome (including failures) so a re-ingest doesn't re-fire
    the network call. TTL: 7 days. Use `use_cache=False` to force re-fetch.
    """
    if not url:
        return None

    if use_cache:
        cached = _cache_get(url)
        if cached:
            if cached["status"] == "ok" and cached["body"]:
                return ExtractResult(
                    body=cached["body"],
                    title=cached["title"] or "",
                    publisher_url=cached["publisher_url"] or url,
                    extracted_at=cached["extracted_at"],
                    char_count=cached["char_count"] or len(cached["body"]),
                )
            # Cached failure — respect TTL, don't retry yet
            return None

    publisher_url = resolve_publisher_url(url, timeout=timeout)
    if not publisher_url:
        _cache_put(url, status="failed", result=None)
        return None

    try:
        resp = requests.get(
            publisher_url, headers=_REQUEST_HEADERS, timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        logger.warning(
            "extract_full_text: GET failed %s: %s", publisher_url[:80], exc,
        )
        _cache_put(url, status="failed", result=None)
        return None

    if resp.status_code >= 400:
        logger.info(
            "extract_full_text: %s returned HTTP %s — skipping",
            publisher_url[:80], resp.status_code,
        )
        _cache_put(url, status="failed", result=None)
        return None

    html = resp.text or ""
    # Use response.url (post-redirect) as the canonical URL
    final_url = resp.url or publisher_url

    extracted = _extract_with_trafilatura(html, final_url)
    if extracted is None or len(extracted[1]) < _MIN_BODY_CHARS:
        extracted = _extract_with_bs4(html)

    if extracted is None:
        _cache_put(url, status="failed", result=None)
        return None

    title, body = extracted
    body = (body or "").strip()
    if len(body) < _MIN_BODY_CHARS:
        _cache_put(url, status="too_short", result=None)
        return None

    if _is_paywall_stub(body):
        _cache_put(url, status="paywall", result=None)
        return None

    result = ExtractResult(
        body=body,
        title=title or "",
        publisher_url=final_url,
        extracted_at=time.time(),
        char_count=len(body),
    )
    _cache_put(url, status="ok", result=result)
    return result


# ──────────────────────────────────────────────────────────────────────────
# Backfill — repair existing input/output files
# ──────────────────────────────────────────────────────────────────────────


def backfill_input_file(path: Path, *, min_body: int = _MIN_BODY_CHARS) -> bool:
    """Re-extract the body for a stored raw-input article file.

    Reads the JSON, checks `content`/`summary` length, and if it's
    headline-only (< min_body chars) attempts a full-text extraction.
    On success, MUTATES the file in place: `content` ← extracted body,
    `summary` ← first 500 chars, `metadata.full_text_source` set.

    Returns True when the file was updated; False when no update was needed
    (already has body) or extraction failed.
    """
    import json
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("backfill: cannot read %s: %s", path.name, exc)
        return False

    existing = (d.get("content") or "").strip()
    title = (d.get("title") or "").strip()
    # Already has substantive body
    if len(existing) >= min_body and existing != title and len(existing) > len(title) + 50:
        return False

    url = d.get("url") or ""
    if not url:
        return False

    result = extract_full_text(url)
    if result is None:
        return False

    d["content"] = result.body
    d["summary"] = result.body[:500]
    meta = d.get("metadata") or {}
    meta["full_text_source"] = "publisher_scrape"
    meta["full_text_char_count"] = result.char_count
    meta["publisher_url"] = result.publisher_url
    meta["full_text_extracted_at"] = result.extracted_at
    d["metadata"] = meta

    path.write_text(json.dumps(d, indent=2), encoding="utf-8")
    logger.info(
        "backfill: %s — %d → %d chars",
        path.name, len(existing), result.char_count,
    )
    return True
