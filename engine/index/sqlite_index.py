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
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from engine.config import get_data_path

logger = logging.getLogger(__name__)

DB_PATH = get_data_path("snowkap.db")

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
"""


# ---------------------------------------------------------------------------
# Connection + schema
# ---------------------------------------------------------------------------


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_WAL_ENABLED = False


def _ensure_wal_mode() -> None:
    """Phase 11A: enable SQLite WAL mode so concurrent readers + one writer
    don't lock. Idempotent — `PRAGMA journal_mode` is a per-DB persistent
    setting so this runs once at first-access. `synchronous=FULL` (SQLite
    default) is kept for durability; in WAL mode it's still fast because
    fsync happens at commit + checkpoint, not per-page.

    The crit-win is moving from default `DELETE` rollback-journal mode to
    `WAL`: reads never block writes + writes never block reads."""
    global _WAL_ENABLED
    if _WAL_ENABLED:
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
) -> list[dict[str, Any]]:
    """Return the feed ordered by relevance DESC, published_at DESC."""
    ensure_schema()
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if company_slug:
        clauses.append("company_slug = :company_slug")
        params["company_slug"] = company_slug
    if tier:
        clauses.append("tier = :tier")
        params["tier"] = tier
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


def get_by_id(article_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM article_index WHERE id = ?", (article_id,)
        ).fetchone()
        return dict(row) if row else None


def count(company_slug: str | None = None, tier: str | None = None) -> int:
    ensure_schema()
    clauses: list[str] = []
    params: list[Any] = []
    if company_slug:
        clauses.append("company_slug = ?")
        params.append(company_slug)
    if tier:
        clauses.append("tier = ?")
        params.append(tier)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM article_index {where}", params).fetchone()[0])


def distinct_companies() -> list[str]:
    ensure_schema()
    with _connect() as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT company_slug FROM article_index").fetchall()]


def count_high_impact(company_slug: str | None = None) -> int:
    """Count articles with materiality CRITICAL/HIGH or relevance_score >= 5."""
    ensure_schema()
    clause = "WHERE (materiality IN ('CRITICAL', 'HIGH') OR relevance_score >= 5.0)"
    params: list[Any] = []
    if company_slug:
        clause += " AND company_slug = ?"
        params.append(company_slug)
    with _connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM article_index {clause}", params).fetchone()[0])


def count_new_last_24h(company_slug: str | None = None) -> int:
    """Count articles published in the last 24 hours."""
    ensure_schema()
    clauses = ["published_at >= datetime('now', '-1 day')"]
    params: list[Any] = []
    if company_slug:
        clauses.append("company_slug = ?")
        params.append(company_slug)
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
        params.append(company_slug)
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
