"""POW-2b — Industry-shared article pool model.

CRUD helpers over the `article_pool` table (migration 011). Backend-
agnostic (SQLite dev / Postgres prod via `engine.db.connect`).

The article_pool is the canonical store for every fetched article going
forward. One row per unique URL. The `material_industries` JSONB array
determines which companies see the article on their /now deck.

See: docs/POWER_OF_NOW_ARCHITECTURE.md §3.1 + §4.1.

Public surface:
  * upsert(article_id, url, title, source, published_at, primary_industry,
           material_industries, primary_pillar, primary_theme, event_id,
           event_polarity, shared_analysis) → ArticlePoolRow
  * get(article_id) → ArticlePoolRow | None
  * get_by_url(url) → ArticlePoolRow | None
  * list_for_industry(industry, max_age_days=30, limit=50) → list[ArticlePoolRow]
  * compute_material_industries(theme, industries) → list[str]
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from engine.db import connect as _db_connect, is_postgres

logger = logging.getLogger(__name__)

CURRENT_POOL_SCHEMA_VERSION = "p1.0-pool"

# Default materiality threshold for inclusion in material_industries.
# Anything below this is treated as "not material enough to surface on
# this industry's deck". Mirrors the existing Phase-32 industry-tagging
# threshold.
_MATERIAL_INDUSTRY_THRESHOLD = 0.4


@dataclass
class ArticlePoolRow:
    id: str
    url: str
    title: str
    source: str | None
    published_at: str | None
    fetched_at: str
    primary_industry: str
    material_industries: list[str]
    primary_pillar: str | None
    primary_theme: str | None
    event_id: str | None
    event_polarity: str | None
    shared_analysis: dict[str, Any] = field(default_factory=dict)
    schema_version: str = CURRENT_POOL_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "source": self.source,
            "published_at": self.published_at,
            "fetched_at": self.fetched_at,
            "primary_industry": self.primary_industry,
            "material_industries": self.material_industries,
            "primary_pillar": self.primary_pillar,
            "primary_theme": self.primary_theme,
            "event_id": self.event_id,
            "event_polarity": self.event_polarity,
            "shared_analysis": self.shared_analysis,
            "schema_version": self.schema_version,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _to_jsonb_param(value: Any) -> Any:
    """Postgres expects a JSON string for JSONB columns; SQLite expects TEXT.

    `psycopg2`'s Json adapter would also work but adds a dependency on
    a specific path. JSON-text is portable across both backends.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _from_jsonb_value(value: Any) -> Any:
    """psycopg2 returns JSONB columns as already-parsed dict/list. SQLite
    returns the raw TEXT. Normalize both to Python objects."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
    return value


def _row_to_article_pool(row: Any) -> ArticlePoolRow:
    def _get(name: str, idx: int) -> Any:
        if hasattr(row, "keys"):
            return row[name]
        return row[idx]

    material = _from_jsonb_value(_get("material_industries", 7)) or []
    shared = _from_jsonb_value(_get("shared_analysis", 12)) or {}
    return ArticlePoolRow(
        id=_get("id", 0),
        url=_get("url", 1),
        title=_get("title", 2),
        source=_get("source", 3),
        published_at=_get("published_at", 4),
        fetched_at=_get("fetched_at", 5),
        primary_industry=_get("primary_industry", 6),
        material_industries=material if isinstance(material, list) else [],
        primary_pillar=_get("primary_pillar", 8),
        primary_theme=_get("primary_theme", 9),
        event_id=_get("event_id", 10),
        event_polarity=_get("event_polarity", 11),
        shared_analysis=shared if isinstance(shared, dict) else {},
        schema_version=_get("schema_version", 13),
    )


# ─── Write path ────────────────────────────────────────────────────────────

def upsert(
    *,
    article_id: str,
    url: str,
    title: str,
    source: str | None,
    published_at: str | None,
    primary_industry: str,
    material_industries: list[str],
    primary_pillar: str | None,
    primary_theme: str | None,
    event_id: str | None,
    event_polarity: str | None,
    shared_analysis: dict[str, Any],
) -> ArticlePoolRow:
    """Insert or update a row keyed by `article_id`.

    The (url) UNIQUE constraint guarantees URL-dedup at the DB level
    even if two pipeline runs pass slightly different article_ids for
    the same canonical URL.
    """
    if not article_id:
        raise ValueError("article_id is required")
    if not url:
        raise ValueError("url is required")
    if not primary_industry:
        raise ValueError("primary_industry is required (Stage-4 relevance scorer must set it)")

    # Normalize material_industries — ensure primary_industry is always included.
    mi = list(material_industries or [])
    if primary_industry not in mi:
        mi.insert(0, primary_industry)
    # Dedup while preserving order
    seen: set[str] = set()
    mi = [i for i in mi if i and not (i in seen or seen.add(i))]

    now = _now_iso()
    with _db_connect() as conn:
        if is_postgres():
            sql = (
                "INSERT INTO article_pool ("
                "  id, url, title, source, published_at, fetched_at,"
                "  primary_industry, material_industries, primary_pillar,"
                "  primary_theme, event_id, event_polarity, shared_analysis,"
                "  schema_version"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?, ?, ?, ?, ?::jsonb, ?)"
                "ON CONFLICT (id) DO UPDATE SET"
                "  url = EXCLUDED.url,"
                "  title = EXCLUDED.title,"
                "  source = EXCLUDED.source,"
                "  published_at = EXCLUDED.published_at,"
                "  primary_industry = EXCLUDED.primary_industry,"
                "  material_industries = EXCLUDED.material_industries,"
                "  primary_pillar = EXCLUDED.primary_pillar,"
                "  primary_theme = EXCLUDED.primary_theme,"
                "  event_id = EXCLUDED.event_id,"
                "  event_polarity = EXCLUDED.event_polarity,"
                "  shared_analysis = EXCLUDED.shared_analysis,"
                "  schema_version = EXCLUDED.schema_version"
            )
        else:
            sql = (
                "INSERT OR REPLACE INTO article_pool ("
                "  id, url, title, source, published_at, fetched_at,"
                "  primary_industry, material_industries, primary_pillar,"
                "  primary_theme, event_id, event_polarity, shared_analysis,"
                "  schema_version"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            )
        params = (
            article_id, url, title[:512], source, published_at, now,
            primary_industry, _to_jsonb_param(mi), primary_pillar,
            primary_theme, event_id, event_polarity,
            _to_jsonb_param(shared_analysis or {}),
            CURRENT_POOL_SCHEMA_VERSION,
        )
        conn.execute(sql, params)

    return ArticlePoolRow(
        id=article_id, url=url, title=title, source=source,
        published_at=published_at, fetched_at=now,
        primary_industry=primary_industry, material_industries=mi,
        primary_pillar=primary_pillar, primary_theme=primary_theme,
        event_id=event_id, event_polarity=event_polarity,
        shared_analysis=shared_analysis or {},
    )


# ─── Read path ─────────────────────────────────────────────────────────────

_SELECT_COLS = (
    "id, url, title, source, published_at, fetched_at, "
    "primary_industry, material_industries, primary_pillar, "
    "primary_theme, event_id, event_polarity, shared_analysis, schema_version"
)


def get(article_id: str) -> ArticlePoolRow | None:
    if not article_id:
        return None
    with _db_connect() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM article_pool WHERE id = ?",
            (article_id,),
        ).fetchone()
    return _row_to_article_pool(row) if row else None


def get_by_url(url: str) -> ArticlePoolRow | None:
    if not url:
        return None
    with _db_connect() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM article_pool WHERE url = ?",
            (url,),
        ).fetchone()
    return _row_to_article_pool(row) if row else None


def list_for_industry(
    industry: str,
    max_age_days: int = 30,
    limit: int = 50,
) -> list[ArticlePoolRow]:
    """Return article_pool rows where `industry` is in `material_industries`,
    newest-first, within the freshness window.

    Used by `/api/now/feed` (POW-4) to populate the per-company deck.
    """
    if not industry:
        return []
    with _db_connect() as conn:
        if is_postgres():
            # JSONB containment: faster + uses the GIN index.
            sql = (
                f"SELECT {_SELECT_COLS} FROM article_pool "
                "WHERE material_industries @> ?::jsonb "
                "AND published_at >= (NOW() - (? || ' days')::interval) "
                "ORDER BY published_at DESC NULLS LAST LIMIT ?"
            )
            params: tuple[Any, ...] = (json.dumps([industry]), str(max_age_days), limit)
        else:
            # SQLite — naive LIKE fallback (good enough for tests).
            sql = (
                f"SELECT {_SELECT_COLS} FROM article_pool "
                "WHERE material_industries LIKE ? "
                "ORDER BY published_at DESC LIMIT ?"
            )
            params = (f"%{industry}%", limit)
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_article_pool(r) for r in rows]


# ─── Helpers ───────────────────────────────────────────────────────────────

def compute_material_industries(
    theme: str | None,
    candidate_industries: Iterable[str],
    threshold: float = _MATERIAL_INDUSTRY_THRESHOLD,
) -> list[str]:
    """Return the subset of `candidate_industries` for which the ontology
    materiality weight of `theme` exceeds `threshold`.

    Wraps `engine.ontology.intelligence.query_materiality_weight` so the
    same gate the pipeline uses for relevance scoring decides industry
    visibility on the pool.

    Falls back to `[]` if the ontology import fails (defensive — the
    writer can still set primary_industry alone, which is the floor).
    """
    if not theme:
        return []
    try:
        from engine.ontology.intelligence import query_materiality_weight
    except ImportError:
        logger.warning("compute_material_industries: ontology not importable")
        return []
    out: list[str] = []
    for ind in candidate_industries:
        if not ind:
            continue
        try:
            weight = query_materiality_weight(theme, ind)
        except Exception as exc:  # noqa: BLE001
            logger.debug("materiality_weight failed for (%s, %s): %s", theme, ind, exc)
            continue
        if isinstance(weight, (int, float)) and float(weight) >= threshold:
            out.append(ind)
    return out
