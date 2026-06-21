"""Build the on-disk 3-tier wiki from the DB at app startup.

Why this exists
---------------
``scripts/build_wiki.py`` builds the wiki from ``data/outputs/*/insights/*.json``
on disk. In production we persist insights to Postgres (``article_pool`` +
``company_article_view``) AND run on Railway's *ephemeral* filesystem, so
``data/outputs`` is empty after every deploy and ``wiki_root`` never exists →
``/api/wiki/*`` returns ``{"wiki_root_missing": true}`` (the orphaned-wiki
finding).

This module reconstructs the flattened insight dicts the wiki builders expect
(identical key shape to ``scripts.build_wiki._flatten_insight``) directly from
the DB, then runs the system + tenant tier builders so ``wiki_root`` is
populated. It is wired into ``api/main.py`` startup as a NON-FATAL background
thread — it must never block boot or crash the app.

Idempotent-ish: skips when ``wiki_root`` already has ``*.md`` content (the same
container booting twice), rebuilds when it's missing (fresh deploy).
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)


def load_insights_from_db() -> list[dict[str, Any]]:
    """Flattened wiki-insight dicts for every (article, company) pair.

    Mirrors ``scripts.build_wiki._flatten_insight`` output keys so the
    existing ``build_system_tier`` / ``build_tenant_tier`` consume it unchanged.
    Sourced from ``article_pool`` ⋈ ``company_article_view`` (the same join the
    deck uses), across ALL companies.
    """
    from engine.db import connect
    from engine.models.company_article_view import _from_jsonb_value

    out: list[dict[str, Any]] = []
    with connect() as c:
        cur = c.execute(
            "SELECT a.id, a.url, a.title, a.published_at, a.primary_theme, "
            "a.event_id, a.shared_analysis, v.company_slug, "
            "v.personalised_analysis "
            "FROM article_pool a "
            "JOIN company_article_view v ON v.article_id = a.id"
        )
        rows = cur.fetchall()

    for r in rows:
        try:
            shared = _from_jsonb_value(r[6]) or {}
            personal = _from_jsonb_value(r[8]) or {}
            if not isinstance(personal, dict):
                personal = {}
            why = personal.get("why_it_matters") if isinstance(personal.get("why_it_matters"), dict) else {}
            crit_sum = (why.get("criticality_summary") or "").strip()
            lede_block = personal.get("lede") if isinstance(personal.get("lede"), dict) else {}
            lede_txt = (lede_block.get("text") or "").strip()
            explicit = (personal.get("tier") or "").strip().lower()
            tier = explicit if explicit in ("critical", "light") else ("critical" if lede_txt else "light")

            themes: list[str] = []
            if r[4]:
                themes.append(str(r[4]))
            if isinstance(shared, dict):
                sblock = shared.get("themes")
                if isinstance(sblock, dict):
                    sec = sblock.get("secondary_themes")
                    if isinstance(sec, list):
                        themes.extend(str(t) for t in sec if t)
                elif isinstance(sblock, list):
                    themes.extend(str(t) for t in sblock if t)

            out.append({
                "article_id": str(r[0] or ""),
                "tenant_slug": str(r[7] or ""),
                "url": r[1] or "",
                "title": r[2] or "",
                "published_at": str(r[3] or ""),
                "summary": (crit_sum or "")[:1000],
                "themes": themes,
                "event_id": str(r[5] or ""),
                "materiality": "",
                "tier": tier,
                "decision_summary": {},
            })
        except Exception:
            logger.exception("wiki db_loader: failed to flatten row; skipping")
            continue
    return out


def build_wiki_from_db() -> dict[str, Any]:
    """Synchronous build of the system + all tenant tiers from DB insights.

    Returns a small summary dict. A per-tenant failure is logged and skipped
    (one bad tenant must not abort the whole build).
    """
    from engine.wiki.system_builder import build_system_tier
    from engine.wiki.tenant_builder import build_tenant_tier

    insights = load_insights_from_db()
    if not insights:
        logger.warning("wiki build: 0 insights from DB — nothing to build")
        return {"insights": 0, "tenants": 0, "system_articles": 0}

    sysres = build_system_tier(insights)
    tenants = sorted({i["tenant_slug"] for i in insights if i.get("tenant_slug")})
    built = 0
    for slug in tenants:
        try:
            build_tenant_tier(tenant_slug=slug, insights=insights, competitors=[])
            built += 1
        except Exception:
            logger.exception("wiki build: tenant %s failed", slug)

    summary = {
        "insights": len(insights),
        "tenants": built,
        "system_articles": getattr(sysres, "articles_written", -1),
    }
    logger.info("wiki build from DB complete: %s", summary)
    return summary


def maybe_build_wiki_on_startup() -> None:
    """Non-fatal, backgrounded wiki build. Wire into app startup.

    Gated by ``SNOWKAP_WIKI_BUILD_ON_STARTUP`` (default ``"1"``). Spawns a
    daemon thread so it never blocks boot, skips when ``wiki_root`` already has
    content, and swallows every exception (the wiki is non-critical — a build
    failure must never take down the API).
    """
    if os.environ.get("SNOWKAP_WIKI_BUILD_ON_STARTUP", "1") != "1":
        logger.info("wiki startup build disabled via SNOWKAP_WIKI_BUILD_ON_STARTUP")
        return

    def _runner() -> None:
        try:
            from engine.wiki.paths import wiki_root
            root = wiki_root()
            if root.exists() and any(root.rglob("*.md")):
                logger.info("wiki startup build: wiki_root already populated — skipping")
                return
            build_wiki_from_db()
        except Exception:
            logger.exception("wiki startup build failed (non-fatal)")

    threading.Thread(target=_runner, name="wiki-startup-build", daemon=True).start()
    logger.info("wiki startup build: background thread spawned")
