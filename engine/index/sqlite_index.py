"""SQLite index over the JSON insight files in ``data/outputs/``.

This is an index only — the JSON files remain the source of truth. The
index exists so the thin FastAPI layer (Phase 9) can answer feed / filter /
detail queries in < 50 ms without scanning directories.

Schema::

    article_index(
        id TEXT PRIMARY KEY,
        company_slug TEXT NOT NULL,
        title TEXT NOT NULL,
        source TEXT,
        url TEXT,
        published_at TEXT,
        tier TEXT,               -- HOME | SECONDARY | REJECTED
        materiality TEXT,        -- CRITICAL | HIGH | MODERATE | LOW | NON-MATERIAL
        action TEXT,             -- ACT | MONITOR | IGNORE
        relevance_score REAL,
        impact_score REAL,
        esg_pillar TEXT,
        primary_theme TEXT,
        content_type TEXT,
        framework_count INTEGER,
        do_nothing INTEGER,      -- 0/1
        recommendations_count INTEGER,
        json_path TEXT NOT NULL, -- relative path under data/outputs/
        written_at TEXT,
        ontology_queries INTEGER
    )

Indexes: (company_slug, tier, relevance_score), (company_slug, published_at),
(tier), (content_type).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from engine.config import get_data_path
from engine.db import connect as _db_connect, get_backend, is_postgres

logger = logging.getLogger(__name__)

# Phase 24 — DB_PATH is still emitted for back-compat callers (e.g. the
# smoke test that checks WAL mode on the SQLite file). It points at the
# legacy SQLite location regardless of backend; in Postgres mode it's
# unused.
DB_PATH = get_data_path("snowkap.db")

# Default freshness window for the feed and dashboard counters. Articles
# older than this are hidden from /news/feed, /news/stats and the
# high-impact / total counters so the prospect only ever sees recent
# signals. Tunable via env var without a code change. Set to 0 (or any
# value <= 0) to disable the filter entirely (legacy behaviour, every
# row visible).
def _parse_feed_max_age_days(raw: str | None, default: int = 20) -> int:
    """Safely parse SNOWKAP_FEED_MAX_AGE_DAYS at import time.

    Falls back to ``default`` (and emits a warning) on missing or
    malformed values so a typo in the env var can never break startup.
    """
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        logger.warning(
            "Invalid SNOWKAP_FEED_MAX_AGE_DAYS=%r; falling back to %d",
            raw,
            default,
        )
        return default


FEED_MAX_AGE_DAYS = _parse_feed_max_age_days(
    os.environ.get("SNOWKAP_FEED_MAX_AGE_DAYS")
)


def _freshness_clause(max_age_days: int | None = None) -> tuple[str, str] | None:
    """Return ``(sql_fragment, modifier_value)`` for the freshness filter,
    or ``None`` when the filter is disabled.

    Centralised so query_feed / count / count_high_impact stay in sync
    on the cutoff. Pass ``max_age_days=0`` to bypass the filter for a
    specific call (e.g. an admin "show everything" view).
    """
    days = FEED_MAX_AGE_DAYS if max_age_days is None else max_age_days
    if not days or days <= 0:
        return None
    return ("published_at >= datetime('now', ?)", f"-{int(days)} days")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS article_index (
    id                    TEXT PRIMARY KEY,
    company_slug          TEXT NOT NULL,
    title                 TEXT NOT NULL,
    source                TEXT,
    url                   TEXT,
    published_at          TEXT,
    tier                  TEXT,
    materiality           TEXT,
    action                TEXT,
    relevance_score       REAL,
    impact_score          REAL,
    esg_pillar            TEXT,
    primary_theme         TEXT,
    content_type          TEXT,
    framework_count       INTEGER DEFAULT 0,
    do_nothing            INTEGER DEFAULT 0,
    recommendations_count INTEGER DEFAULT 0,
    json_path             TEXT NOT NULL,
    written_at            TEXT,
    ontology_queries      INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_company_tier
    ON article_index(company_slug, tier, relevance_score DESC);

CREATE INDEX IF NOT EXISTS idx_company_published
    ON article_index(company_slug, published_at DESC);

CREATE INDEX IF NOT EXISTS idx_tier
    ON article_index(tier);

CREATE INDEX IF NOT EXISTS idx_content_type
    ON article_index(content_type);

-- Phase 22.1 — Alias slugs for tenants whose login-time slug ("puma", from
-- the email domain) differs from the canonical slug yfinance assigns
-- ("puma-se" for "PUMA SE"). The login JWT is bound to the alias, but
-- the analysis pipeline writes article_index rows under the canonical
-- slug. resolve_slug() unifies them at read time so the user's queries
-- find their own data without re-issuing the JWT.
CREATE TABLE IF NOT EXISTS slug_aliases (
    alias     TEXT PRIMARY KEY,
    canonical TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Connection + schema
# ---------------------------------------------------------------------------


@contextmanager
def _connect() -> Iterator[Any]:
    """Yield a backend-aware connection.

    Phase 24 — switched from raw ``sqlite3.connect()`` to the
    :mod:`engine.db` abstraction, which routes to either SQLite or
    Postgres based on ``SNOWKAP_DB_BACKEND``. Call sites use the same
    sqlite-flavoured SQL (``?`` placeholders, ``datetime('now', ...)``,
    ``INSERT OR REPLACE``); the dialect translator rewrites them at
    execute time when the active backend is Postgres.
    """
    with _db_connect() as conn:
        yield conn


_WAL_ENABLED = False


def _ensure_wal_mode() -> None:
    """Phase 11A: enable SQLite WAL mode (no-op when running on Postgres).

    Idempotent — runs once at first-access. WAL is a sqlite-only setting;
    on Postgres the call is short-circuited because Postgres has its own
    WAL implementation enabled by default.
    """
    global _WAL_ENABLED
    if _WAL_ENABLED:
        return
    if is_postgres():
        # Postgres has WAL on by default; no client-side toggle needed.
        _WAL_ENABLED = True
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        mode = conn.execute("PRAGMA journal_mode=WAL;").fetchone()
        if mode and mode[0].lower() != "wal":
            logger.warning("sqlite_index: failed to enable WAL (got %r)", mode[0])
        conn.commit()
    _WAL_ENABLED = True


def ensure_schema() -> None:
    _ensure_wal_mode()
    with _connect() as conn:
        conn.executescript(SCHEMA_SQL)


def purge_rejected_articles(older_than_days: int = 90) -> int:
    """Phase 11D: trim REJECTED articles older than N days. Keep HOME +
    SECONDARY forever (they're intellectual property).

    Returns the number of rows deleted. Safe to run from cron:
        0 3 * * 0 python -m engine.index.sqlite_index purge-rejected
    """
    ensure_schema()
    cutoff_sql = f"datetime('now', '-{int(older_than_days)} days')"
    with _connect() as conn:
        result = conn.execute(
            f"""
            DELETE FROM article_index
            WHERE tier = 'REJECTED'
              AND COALESCE(written_at, published_at, '') != ''
              AND COALESCE(written_at, published_at) < {cutoff_sql}
            """
        )
        deleted = result.rowcount
    if deleted > 0:
        logger.info("purge_rejected_articles: removed %d REJECTED rows older than %d days",
                    deleted, older_than_days)
    return deleted


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


def _extract_fields(insight_payload: dict[str, Any]) -> dict[str, Any]:
    """Flatten the combined insight payload into index row fields."""
    article = insight_payload.get("article") or {}
    pipeline = insight_payload.get("pipeline") or {}
    insight = insight_payload.get("insight") or {}
    recs = insight_payload.get("recommendations") or {}
    perspectives = insight_payload.get("perspectives") or {}
    meta = insight_payload.get("meta") or {}

    decision = (insight.get("decision_summary") or {}) if insight else {}
    relevance = (pipeline.get("relevance") or {}) if pipeline else {}
    themes = (pipeline.get("themes") or {}) if pipeline else {}

    # Pull do_nothing from any perspective (they all agree)
    do_nothing = False
    for lens in ("cfo", "ceo", "esg-analyst"):
        p = perspectives.get(lens) or {}
        if p.get("do_nothing"):
            do_nothing = True
            break

    recs_count = 0
    if recs and not recs.get("do_nothing"):
        recs_count = len(recs.get("recommendations") or [])

    return {
        "id": article.get("id", ""),
        "company_slug": article.get("company_slug", ""),
        "title": article.get("title", ""),
        "source": article.get("source", ""),
        "url": article.get("url", ""),
        "published_at": article.get("published_at", ""),
        "tier": pipeline.get("tier", ""),
        "materiality": decision.get("materiality", ""),
        "action": decision.get("action", ""),
        "relevance_score": float(relevance.get("adjusted_total") or 0.0),
        "impact_score": float(insight.get("impact_score") or 0.0) if insight else 0.0,
        "esg_pillar": themes.get("primary_pillar", ""),
        "primary_theme": themes.get("primary_theme", ""),
        "content_type": (pipeline.get("nlp") or {}).get("content_type", ""),
        "framework_count": len(pipeline.get("frameworks") or []),
        "do_nothing": 1 if do_nothing else 0,
        "recommendations_count": recs_count,
        "written_at": meta.get("written_at", ""),
        "ontology_queries": int(pipeline.get("ontology_query_count") or 0),
    }


def upsert_article(insight_payload: dict[str, Any], json_path: Path | str) -> None:
    """Insert or update an index row from a written insight payload."""
    ensure_schema()
    fields = _extract_fields(insight_payload)
    if not fields["id"] or not fields["company_slug"]:
        logger.warning("sqlite_index: skipping row with missing id/company_slug")
        return

    # Store a repo-relative path so the index is portable across machines
    rel_path = str(Path(json_path).resolve().relative_to(get_data_path().parent.resolve())) if Path(json_path).is_absolute() else str(json_path)
    fields["json_path"] = rel_path.replace("\\", "/")

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO article_index (
                id, company_slug, title, source, url, published_at,
                tier, materiality, action, relevance_score, impact_score,
                esg_pillar, primary_theme, content_type, framework_count,
                do_nothing, recommendations_count, json_path, written_at,
                ontology_queries
            ) VALUES (
                :id, :company_slug, :title, :source, :url, :published_at,
                :tier, :materiality, :action, :relevance_score, :impact_score,
                :esg_pillar, :primary_theme, :content_type, :framework_count,
                :do_nothing, :recommendations_count, :json_path, :written_at,
                :ontology_queries
            )
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                source = excluded.source,
                published_at = excluded.published_at,
                tier = excluded.tier,
                materiality = excluded.materiality,
                action = excluded.action,
                relevance_score = excluded.relevance_score,
                impact_score = excluded.impact_score,
                esg_pillar = excluded.esg_pillar,
                primary_theme = excluded.primary_theme,
                content_type = excluded.content_type,
                framework_count = excluded.framework_count,
                do_nothing = excluded.do_nothing,
                recommendations_count = excluded.recommendations_count,
                json_path = excluded.json_path,
                written_at = excluded.written_at,
                ontology_queries = excluded.ontology_queries
            """,
            fields,
        )


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def query_feed(
    company_slug: str | None = None,
    tier: str | None = None,
    limit: int = 20,
    offset: int = 0,
    max_age_days: int | None = None,
) -> list[dict[str, Any]]:
    """Return the feed ordered by relevance DESC, published_at DESC.

    Articles older than ``max_age_days`` (default ``FEED_MAX_AGE_DAYS``,
    20 days) are hidden so the prospect only sees recent signals. Pass
    ``max_age_days=0`` to disable the filter for an admin/debug view.
    """
    ensure_schema()
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if company_slug:
        clauses.append("company_slug = :company_slug")
        params["company_slug"] = resolve_slug(company_slug)
    if tier:
        clauses.append("tier = :tier")
        params["tier"] = tier
    fresh = _freshness_clause(max_age_days)
    if fresh:
        clauses.append(fresh[0].replace("?", ":__age"))
        params["__age"] = fresh[1]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT * FROM article_index
        {where}
        ORDER BY relevance_score DESC, published_at DESC
        LIMIT :limit OFFSET :offset
    """
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def register_alias(alias: str, canonical: str) -> None:
    """Map an alias slug to its canonical slug.

    Phase 22.1 — called from `_background_onboard` once yfinance returns
    the canonical company name (e.g. login slug "puma" → canonical
    "puma-se"). All subsequent read queries against `alias` are
    transparently rewritten to `canonical` via `resolve_slug`. A no-op
    when alias == canonical.
    """
    if not alias or not canonical or alias == canonical:
        return
    ensure_schema()
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO slug_aliases (alias, canonical) VALUES (?, ?)",
            (alias, canonical),
        )


def mirror_to_slug(canonical: str, alias: str) -> int:
    """Phase 22.2 — Make `article_index` rows owned by `canonical` visible
    when queried by `alias`.

    Implemented as a thin wrapper around `register_alias` because
    `article_index.id` is the PRIMARY KEY: physically duplicating rows
    under the alias `company_slug` would force synthetic IDs and fan
    out to every downstream lookup (`get_by_id`, `_require_article_in_scope`,
    on-demand enrichment, etc.). The read-time alias rewrite via
    `resolve_slug` is uniformly applied by every read helper in this
    module, achieving the same user-visible outcome (the alias-bound
    session sees the canonical's articles) without duplication risk.

    Returns the count of canonical rows that the alias now resolves
    to — useful for callers (and tests) that want a sanity number.
    Safe to call repeatedly; no-op when alias == canonical.
    """
    if not alias or not canonical or alias == canonical:
        return 0
    register_alias(alias, canonical)
    return count(company_slug=canonical)


def resolve_slug(slug: str | None) -> str | None:
    """Return the canonical slug for `slug`, or `slug` itself if it isn't
    an alias. Read-only — never raises. None passes through."""
    if not slug:
        return slug
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT canonical FROM slug_aliases WHERE alias = ?", (slug,)
        ).fetchone()
        return row[0] if row else slug


def get_by_id(article_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM article_index WHERE id = ?", (article_id,)
        ).fetchone()
        return dict(row) if row else None


def count(
    company_slug: str | None = None,
    tier: str | None = None,
    pillar: str | None = None,
    content_type: str | None = None,
    max_age_days: int | None = None,
) -> int:
    """Count articles matching the optional filters.

    Phase 22.3 — accepts `pillar` and `content_type` so /news/feed's
    `total` agrees with the actually-rendered list when those filters
    are set. Pre-fix, the feed handler post-filtered rows in Python
    while `count()` ignored those filters, so the UI saw "5 total"
    next to an empty list.

    Also honours the freshness window (default 20 days) so the
    "Articles" tile on the dashboard agrees with the visible feed.
    """
    ensure_schema()
    clauses: list[str] = []
    params: list[Any] = []
    if company_slug:
        clauses.append("company_slug = ?")
        params.append(resolve_slug(company_slug))
    if tier:
        clauses.append("tier = ?")
        params.append(tier)
    if pillar:
        clauses.append("esg_pillar = ?")
        params.append(pillar)
    if content_type:
        clauses.append("content_type = ?")
        params.append(content_type)
    fresh = _freshness_clause(max_age_days)
    if fresh:
        clauses.append(fresh[0])
        params.append(fresh[1])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM article_index {where}", params).fetchone()[0])


def distinct_companies() -> list[str]:
    ensure_schema()
    with _connect() as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT company_slug FROM article_index").fetchall()]


def count_high_impact(company_slug: str | None = None, max_age_days: int | None = None) -> int:
    """Count articles with materiality CRITICAL/HIGH or relevance_score >= 5.

    Honours the freshness window (default 20 days) so the dashboard
    "High Impact" tile only counts articles still visible in the feed.
    """
    ensure_schema()
    clause = "WHERE (materiality IN ('CRITICAL', 'HIGH') OR relevance_score >= 5.0)"
    params: list[Any] = []
    if company_slug:
        clause += " AND company_slug = ?"
        params.append(resolve_slug(company_slug))
    fresh = _freshness_clause(max_age_days)
    if fresh:
        clause += f" AND {fresh[0]}"
        params.append(fresh[1])
    with _connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM article_index {clause}", params).fetchone()[0])


def count_new_last_24h(company_slug: str | None = None) -> int:
    """Count articles published in the last 24 hours."""
    ensure_schema()
    clauses = ["published_at >= datetime('now', '-1 day')"]
    params: list[Any] = []
    if company_slug:
        clauses.append("company_slug = ?")
        params.append(resolve_slug(company_slug))
    where = "WHERE " + " AND ".join(clauses)
    with _connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM article_index {where}", params).fetchone()[0])


def count_active_signals(company_slug: str | None = None, days: int = 7) -> int:
    """Phase 13 B8 — count of forward-looking risk signals.

    Backs the dashboard's "Active Signals" tile (formerly the always-zero
    "Predictions" stub). Definition: HOME-tier articles published in the
    last `days` days with materiality CRITICAL or HIGH. This is a
    meaningful, non-zero number for any active company and gives the demo
    a credible "live risk surface" feel without needing the full Phase I6
    sentiment-prediction engine.
    """
    ensure_schema()
    clauses = [
        "tier = 'HOME'",
        "(materiality IN ('CRITICAL', 'HIGH') OR relevance_score >= 7.0)",
        "published_at >= datetime('now', ?)",
    ]
    params: list[Any] = [f"-{int(max(1, days))} days"]
    if company_slug:
        clauses.append("company_slug = ?")
        params.append(resolve_slug(company_slug))
    where = "WHERE " + " AND ".join(clauses)
    with _connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM article_index {where}", params).fetchone()[0])


def stats() -> dict[str, Any]:
    ensure_schema()
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM article_index").fetchone()[0]
        by_tier = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT tier, COUNT(*) FROM article_index GROUP BY tier"
            ).fetchall()
        }
        by_company = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT company_slug, COUNT(*) FROM article_index GROUP BY company_slug"
            ).fetchall()
        }
        return {"total": total, "by_tier": by_tier, "by_company": by_company}
