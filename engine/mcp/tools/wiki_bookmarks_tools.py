"""Wiki v1.1 — MCP adapter for `bookmarks-recall`.

Returns the caller's personal bookmark library, enriched with each
underlying article's title + criticality_band so the LLM in `/ask`
can answer:

  - "Summarise what I saved this week"
  - "Which bookmarks haven't I added notes to yet?"
  - "What's the climate trend in my Wiki?"

Per-user isolation: the dispatch layer in `engine.mcp.chat_integration`
passes the JWT `sub` claim into `payload["viewer_email"]` so this tool
can never leak across users.

See: docs/POWER_OF_NOW_ARCHITECTURE.md §3.5, §14.1 (Wiki context type).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def handle_bookmarks_recall(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    """Return the caller's bookmarks, grouped by section.

    Output shape:
        {
            "user_email": str,
            "count": int,
            "by_section": {
                "pinned": [...],
                "climate": [...],
                "capital": [...],
                "social": [...],
                "custom": [...],
            },
            "bookmarks": [  # flat list, sorted by bookmarked_at DESC
                {
                    "article_id": str,
                    "section": str,
                    "note": str | None,
                    "bookmarked_at": str,
                    "title": str | None,
                    "source": str | None,
                    "criticality_band": str | None,
                },
                ...
            ],
        }

    Returns ``{"error": ...}`` when ``viewer_email`` is missing.
    """
    from engine.models import user_bookmarks as _ub
    from engine.index.sqlite_index import get_by_id

    viewer_email = (payload.get("viewer_email") or "").strip().lower()
    if not viewer_email or "@" not in viewer_email:
        return {"error": "viewer_email required (a real email)"}

    section_filter = payload.get("section")
    limit = int(payload.get("limit") or 50)

    rows = _ub.list_for_user(viewer_email, section=section_filter)
    rows = rows[:limit]

    enriched: list[dict[str, Any]] = []
    by_section: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        # Enrich with article metadata so the LLM sees titles, not IDs.
        article_row = None
        try:
            article_row = get_by_id(r.article_id)
        except Exception:  # noqa: BLE001 — best-effort
            article_row = None
        entry = {
            "article_id": r.article_id,
            "section": r.section,
            "note": r.note,
            "bookmarked_at": r.bookmarked_at,
            "title": (article_row or {}).get("title"),
            "source": (article_row or {}).get("source"),
            "criticality_band": (article_row or {}).get("criticality_band"),
        }
        enriched.append(entry)
        by_section.setdefault(r.section, []).append(entry)

    return {
        "user_email": viewer_email,
        "count": len(enriched),
        "by_section": by_section,
        "bookmarks": enriched,
    }
