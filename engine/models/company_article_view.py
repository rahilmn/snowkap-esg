"""POW-2c — Per-(article, company) personalised view model.

CRUD helpers over the `company_article_view` table (migration 011).
Holds the per-company narrative computed by pipeline Stages 11-12:
- why_it_matters.stakes_for_company
- why_it_matters.financial_exposure (engine cascade with company β)
- what_it_triggers.recommended_actions (REREACT, polarity-aware)
- what_to_watch.next_decision_window
- methodology (per-bullet provenance)

Plus the criticality scorecard (criticality_score + band) so the deck
can sort by criticality DESC without joining the analysis blob.

See: docs/POWER_OF_NOW_ARCHITECTURE.md §3.2 + §4.1 + §4.4.

Public surface:
  * upsert(article_id, company_slug, personalised_analysis,
           criticality_score, criticality_band) → CompanyArticleViewRow
  * get(article_id, company_slug) → CompanyArticleViewRow | None
  * list_for_company(company_slug, limit=20) → list[CompanyArticleViewRow]
                                                (criticality DESC)
  * deck_for_company(company_slug, industry, max_age_days=30, limit=10)
                                              → list[deck row dict]
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from engine.db import connect as _db_connect, is_postgres

logger = logging.getLogger(__name__)

CURRENT_PERSONALISED_SCHEMA_VERSION = "p1.0-personalised"

_BAND_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "REJECTED": 4}


@dataclass
class CompanyArticleViewRow:
    article_id: str
    company_slug: str
    personalised_analysis: dict[str, Any]
    criticality_score: float
    criticality_band: str
    schema_version: str
    computed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "article_id": self.article_id,
            "company_slug": self.company_slug,
            "personalised_analysis": self.personalised_analysis,
            "criticality_score": self.criticality_score,
            "criticality_band": self.criticality_band,
            "schema_version": self.schema_version,
            "computed_at": self.computed_at,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _to_jsonb_param(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _from_jsonb_value(value: Any) -> Any:
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


def _row_to_view(row: Any) -> CompanyArticleViewRow:
    def _get(name: str, idx: int) -> Any:
        if hasattr(row, "keys"):
            return row[name]
        return row[idx]

    analysis = _from_jsonb_value(_get("personalised_analysis", 2)) or {}
    return CompanyArticleViewRow(
        article_id=_get("article_id", 0),
        company_slug=_get("company_slug", 1),
        personalised_analysis=analysis if isinstance(analysis, dict) else {},
        criticality_score=float(_get("criticality_score", 3) or 0.0),
        criticality_band=_get("criticality_band", 4) or "MEDIUM",
        schema_version=_get("schema_version", 5) or CURRENT_PERSONALISED_SCHEMA_VERSION,
        computed_at=_get("computed_at", 6) or "",
    )


# ─── Write path ────────────────────────────────────────────────────────────

def upsert(
    *,
    article_id: str,
    company_slug: str,
    personalised_analysis: dict[str, Any],
    criticality_score: float,
    criticality_band: str,
) -> CompanyArticleViewRow:
    """Insert or update a (article_id, company_slug) row.

    Caller is responsible for re-computing personalised_analysis when
    upstream inputs change (article facts, company painpoints, etc.).
    The `schema_version` column is bumped automatically; reads can
    invalidate when the version no longer matches CURRENT_*.
    """
    if not article_id:
        raise ValueError("article_id is required")
    if not company_slug:
        raise ValueError("company_slug is required")

    band = (criticality_band or "MEDIUM").upper()
    if band not in _BAND_RANK:
        band = "MEDIUM"

    now = _now_iso()
    with _db_connect() as conn:
        if is_postgres():
            sql = (
                "INSERT INTO company_article_view ("
                "  article_id, company_slug, personalised_analysis,"
                "  criticality_score, criticality_band, schema_version, computed_at"
                ") VALUES (?, ?, ?::jsonb, ?, ?, ?, ?)"
                "ON CONFLICT (article_id, company_slug) DO UPDATE SET"
                "  personalised_analysis = EXCLUDED.personalised_analysis,"
                "  criticality_score = EXCLUDED.criticality_score,"
                "  criticality_band = EXCLUDED.criticality_band,"
                "  schema_version = EXCLUDED.schema_version,"
                "  computed_at = EXCLUDED.computed_at"
            )
        else:
            sql = (
                "INSERT OR REPLACE INTO company_article_view ("
                "  article_id, company_slug, personalised_analysis,"
                "  criticality_score, criticality_band, schema_version, computed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)"
            )
        params = (
            article_id, company_slug,
            _to_jsonb_param(personalised_analysis or {}),
            float(criticality_score or 0.0),
            band,
            CURRENT_PERSONALISED_SCHEMA_VERSION,
            now,
        )
        conn.execute(sql, params)

    return CompanyArticleViewRow(
        article_id=article_id,
        company_slug=company_slug,
        personalised_analysis=personalised_analysis or {},
        criticality_score=float(criticality_score or 0.0),
        criticality_band=band,
        schema_version=CURRENT_PERSONALISED_SCHEMA_VERSION,
        computed_at=now,
    )


# ─── Read path ─────────────────────────────────────────────────────────────

_SELECT_COLS = (
    "article_id, company_slug, personalised_analysis, "
    "criticality_score, criticality_band, schema_version, computed_at"
)


def get(article_id: str, company_slug: str) -> CompanyArticleViewRow | None:
    if not article_id or not company_slug:
        return None
    with _db_connect() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM company_article_view "
            "WHERE article_id = ? AND company_slug = ?",
            (article_id, company_slug),
        ).fetchone()
    return _row_to_view(row) if row else None


def list_for_company(company_slug: str, limit: int = 20) -> list[CompanyArticleViewRow]:
    """Return all personalised rows for a company, criticality DESC."""
    if not company_slug:
        return []
    with _db_connect() as conn:
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM company_article_view "
            "WHERE company_slug = ? ORDER BY criticality_score DESC LIMIT ?",
            (company_slug, limit),
        ).fetchall()
    return [_row_to_view(r) for r in rows]


def deck_for_company(
    company_slug: str,
    industry: str,
    max_age_days: int = 30,
    limit: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The /now deck SQL — joins article_pool ⋈ company_article_view.

    Phase 51 — returns ``(rows, meta)`` where ``meta`` carries completeness
    counts ``{critical_count, light_count, dropped_incomplete}``. Each row also
    carries an explicit ``tier`` ("critical"|"light"), ``has_lede`` and
    ``has_recs`` so the frontend never has to infer the tier.

    Sort: criticality_band rank ASC (CRITICAL first), criticality_score DESC,
    published_at DESC. Limit 10 by default (per O5 spec).
    Filter: published_at >= NOW() - 30d, material_industries contains `industry`.

    Returns merged dicts:
      {
        "article_id": ..., "url": ..., "title": ..., "source": ...,
        "published_at": ..., "primary_industry": ..., "primary_pillar": ...,
        "primary_theme": ..., "event_id": ..., "event_polarity": ...,
        "shared_analysis": {...},
        "personalised_analysis": {...},
        "criticality_score": ..., "criticality_band": ...,
      }

    See: docs/POWER_OF_NOW_ARCHITECTURE.md §4.4.
    """
    if not company_slug or not industry:
        return [], {"critical_count": 0, "light_count": 0, "dropped_incomplete": 0}
    with _db_connect() as conn:
        if is_postgres():
            sql = (
                "SELECT a.id, a.url, a.title, a.source, a.published_at,"
                "  a.primary_industry, a.primary_pillar, a.primary_theme,"
                "  a.event_id, a.event_polarity, a.shared_analysis,"
                "  v.personalised_analysis, v.criticality_score, v.criticality_band "
                "FROM article_pool a "
                "JOIN company_article_view v "
                "  ON v.article_id = a.id "
                "  AND v.company_slug = ? "
                "WHERE a.material_industries @> ?::jsonb "
                # Phase 56.F — freshness gate, with a carve-out for PINNED
                # curated criticals (band=CRITICAL + the forced score 0.9): they
                # bypass the 30-day window so an admin-curated material/negative
                # story (e.g. a product recall published 35 days ago) still shows.
                # Auto-fetched criticals score well under 0.85, so this never
                # resurfaces stale organic articles.
                "  AND (a.published_at >= (NOW() - (? || ' days')::interval) "
                "       OR (v.criticality_band = 'CRITICAL' AND v.criticality_score >= 0.85)) "
                "ORDER BY "
                "  CASE v.criticality_band "
                "    WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 "
                "    WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 3 ELSE 4 END, "
                "  v.criticality_score DESC, "
                "  a.published_at DESC "
                "LIMIT ?"
            )
            params: tuple[Any, ...] = (company_slug, json.dumps([industry]), str(max_age_days), limit)
        else:
            sql = (
                "SELECT a.id, a.url, a.title, a.source, a.published_at,"
                "  a.primary_industry, a.primary_pillar, a.primary_theme,"
                "  a.event_id, a.event_polarity, a.shared_analysis,"
                "  v.personalised_analysis, v.criticality_score, v.criticality_band "
                "FROM article_pool a "
                "JOIN company_article_view v "
                "  ON v.article_id = a.id "
                "  AND v.company_slug = ? "
                "WHERE a.material_industries LIKE ? "
                "ORDER BY v.criticality_score DESC LIMIT ?"
            )
            params = (company_slug, f"%{industry}%", limit)
        rows = conn.execute(sql, params).fetchall()

    out: list[dict[str, Any]] = []
    dropped_incomplete = 0
    critical_count = 0
    for r in rows:
        def _g(name: str, idx: int) -> Any:
            if hasattr(r, "keys"):
                return r[name]
            return r[idx]
        shared = _from_jsonb_value(_g("shared_analysis", 10)) or {}
        personal = _from_jsonb_value(_g("personalised_analysis", 11)) or {}

        # Phase 46.E + Phase 47.K contract: the deck only shows articles
        # with a populated, company-specific criticality_summary.
        #
        # Phase 47.K addition: also drop articles whose criticality_summary
        # is the generic "no single dominant driver" / "ESG-relevant
        # article flagged" literal fallback that fires when Stage 10 LLM
        # failed silently (typically because article body was paywalled
        # and only the headline was available). Showing 5 articles that
        # all say the same generic line is worse than showing 1-2 with
        # real analysis — the user instantly loses trust.
        why = (personal.get("why_it_matters") or {}) if isinstance(personal, dict) else {}
        crit_sum = (why.get("criticality_summary") or "").strip()
        if not crit_sum:
            dropped_incomplete += 1
            continue
        # Phase 47.L — kept all articles whose criticality_summary is
        # populated. The previous version dropped generic-fallback text
        # entirely, which emptied the deck when Stage 10 LLM was failing
        # for many articles. Now we keep them but rely on the new
        # article-specific fallback in role_explainer.build_criticality_summary
        # (uses headline + theme) so each card reads uniquely.

        # Phase 51 — derive an explicit tier so the frontend never has to infer
        # critical-vs-light from lede presence. A critical card carries an
        # editorial lede (+ recs); a light card is a Stage 1-9 watchlist entry.
        lede_txt = ((personal.get("lede") or {}).get("text") or "").strip() if isinstance(personal, dict) else ""
        recs = ((personal.get("what_it_triggers") or {}).get("recommended_actions") or []) if isinstance(personal, dict) else []
        explicit = (personal.get("tier") or "").strip().lower() if isinstance(personal, dict) else ""
        tier = explicit if explicit in ("critical", "light") else ("critical" if lede_txt else "light")
        if tier == "critical":
            critical_count += 1

        out.append({
            "article_id": _g("id", 0),
            "url": _g("url", 1),
            "title": _g("title", 2),
            "source": _g("source", 3),
            "published_at": _g("published_at", 4),
            "primary_industry": _g("primary_industry", 5),
            "primary_pillar": _g("primary_pillar", 6),
            "primary_theme": _g("primary_theme", 7),
            "event_id": _g("event_id", 8),
            "event_polarity": _g("event_polarity", 9),
            "shared_analysis": shared,
            "personalised_analysis": personal,
            "criticality_score": float(_g("criticality_score", 12) or 0.0),
            "criticality_band": _g("criticality_band", 13) or "MEDIUM",
            "tier": tier,
            "has_lede": bool(lede_txt),
            "has_recs": bool(recs),
        })

    if dropped_incomplete:
        logger.info(
            "deck_for_company: filtered %d row(s) for %s missing "
            "criticality_summary (legacy pre-Phase-46 data)",
            dropped_incomplete, company_slug,
        )
    meta = {
        "critical_count": critical_count,
        "light_count": len(out) - critical_count,
        "dropped_incomplete": dropped_incomplete,
    }
    return out, meta


def invalidate_for_company(company_slug: str) -> int:
    """Mark every row for a company stale by clearing schema_version.

    Used when the company's painpoints or financial calibration change
    so the next view triggers a fresh personalisation pass.
    Returns the row count touched.
    """
    if not company_slug:
        return 0
    with _db_connect() as conn:
        cur = conn.execute(
            "UPDATE company_article_view SET schema_version = ? "
            "WHERE company_slug = ?",
            ("invalidated", company_slug),
        )
        return cur.rowcount or 0


def set_band(article_id: str, company_slug: str, *, band: str, score: float) -> int:
    """Phase 56.F — directly UPDATE just the criticality band + score of an
    existing row.

    Used by the curated-deck pin (force band=CRITICAL so the article leads the
    feed sort + bypasses the age window). Unlike re-``upsert``-ing the whole row,
    this never re-writes ``personalised_analysis`` — so a large/edge-case payload
    can't make the band stamp fail (the bug that left a pinned recall age-hidden).
    Returns the row count updated (0 if the row doesn't exist yet).
    """
    if not article_id or not company_slug:
        return 0
    band = (band or "MEDIUM").upper()
    if band not in _BAND_RANK:
        band = "MEDIUM"
    with _db_connect() as conn:
        cur = conn.execute(
            "UPDATE company_article_view "
            "SET criticality_band = ?, criticality_score = ? "
            "WHERE article_id = ? AND company_slug = ?",
            (band, float(score or 0.0), article_id, company_slug),
        )
        return cur.rowcount or 0


def delete_for_company(company_slug: str) -> int:
    """Phase 56.F — hard-delete every per-company view row for a slug.

    The /now feed JOINs article_pool ⋈ company_article_view, so removing the
    view rows hides ALL of a company's cards without touching the shared
    article_pool (other tenants' decks are unaffected). Used by the admin
    curated-ingest path to reset a deck to a clean slate before publishing an
    exact, hand-picked set. Returns the row count removed.
    """
    if not company_slug:
        return 0
    with _db_connect() as conn:
        cur = conn.execute(
            "DELETE FROM company_article_view WHERE company_slug = ?",
            (company_slug,),
        )
        return cur.rowcount or 0
