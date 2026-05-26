"""Forum v1.1 — MCP adapter for `forum-thread`.

Fetches one forum thread + its replies, enriched with author-company
attribution (resolved via tenant_registry on the email domain), so the
LLM in /ask can answer:

  - "Summarise the top positions in this thread"
  - "Suggest a thoughtful reply that ties to my company's painpoints"
  - "Where do peers in other industries weigh in differently?"

Mirrors the `article-comments` handler shape so the Ask chat sees
both tools as interchangeable structured discussion sources.

See: docs/POWER_OF_NOW_ARCHITECTURE.md §14 (Ask-chat context types).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def handle_forum_thread(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    """Return one forum thread + replies, enriched with author-company tags.

    Output shape:
        {
            "thread_id": str,
            "title": str,
            "body": str,
            "tag": str,
            "author": {"display": str, "company": str | None},
            "pinned": bool,
            "created_at": str,
            "deleted": bool,
            "replies": [
                {
                    "id": str,
                    "author": {"display": str, "company": str | None},
                    "body": str,
                    "created_at": str,
                    "deleted": bool,
                },
                ...
            ],
            "reply_count": int,
        }

    Returns ``{"error": ...}`` when the thread is missing.
    """
    from engine.models import forum_threads as _ft
    from engine.index import tenant_registry as _tr

    thread_id = (payload.get("thread_id") or "").strip()
    if not thread_id:
        return {"error": "thread_id required"}

    thread = _ft.get_thread(thread_id)
    if thread is None:
        return {"error": f"thread not found: {thread_id}"}

    def _author_company(email: str | None) -> str | None:
        if not email or "@" not in email:
            return None
        domain = email.split("@", 1)[1].lower()
        try:
            tenant = _tr.get_tenant_by_domain(domain)
        except Exception:  # noqa: BLE001
            return None
        if tenant:
            return tenant.get("name") or tenant.get("slug")
        return None

    replies = _ft.list_replies(thread_id)

    return {
        "thread_id": thread.id,
        "title": thread.title,
        "body": thread.body,
        "tag": thread.tag,
        "author": {
            "display": thread.author_name,
            "company": _author_company(thread.author_email),
        },
        "pinned": bool(thread.pinned),
        "created_at": thread.created_at,
        "deleted": thread.deleted_at is not None,
        "replies": [
            {
                "id": r.id,
                "author": {
                    "display": r.author_name,
                    "company": _author_company(r.author_email),
                },
                "body": r.body if not r.deleted_at else "[deleted by author]",
                "created_at": r.created_at,
                "deleted": r.deleted_at is not None,
            }
            for r in replies
        ],
        "reply_count": len([r for r in replies if not r.deleted_at]),
    }
