"""MCP adapters for article tools.

  - `article-list`: surface recent articles for a tenant.
  - `article-comments` (POW-5b): surface the threaded discussion on a
    given article so the LLM can reason about peer opinions, vote
    scores, and craft reply suggestions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.index.sqlite_index import query_feed


def handle_article_list(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    rows = query_feed(
        company_slug=payload.get("tenant"),
        limit=payload.get("limit", 20),
    )
    # Strip path / internal fields — chat agent shouldn't see filesystem layout
    return {
        "tenant": payload.get("tenant"),
        "articles": [
            {
                k: v for k, v in r.items()
                if k not in {"json_path", "raw_content"}
            }
            for r in rows
        ],
        "count": len(rows),
    }


def handle_article_comments(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    """POW-5b — Return the threaded comment discussion for an article.

    Each comment is enriched with:
      - author_email + author_display
      - author_company (from tenant_registry lookup on the email domain)
      - vote_score + your_vote (relative to the caller, when known)
      - replies (1 level deep, sorted oldest-first)

    This is the canonical data source for the Ask chat to answer
    "where do peer banks disagree on this?" and "draft a reply".
    """
    from engine.models import article_comments as _ac
    from engine.index import tenant_registry as _tr

    article_id = (payload.get("article_id") or "").strip()
    if not article_id:
        return {"article_id": "", "count": 0, "threads": [], "error": "article_id required"}

    viewer_email = (payload.get("viewer_email") or "").strip() or None
    limit = int(payload.get("limit") or 20)

    threads = _ac.list_comments(article_id, viewer_email=viewer_email)

    def _enrich(comment_dict: dict[str, Any]) -> dict[str, Any]:
        # Resolve the author's company via their email domain.
        # Falls back to the email localpart when no tenant is registered.
        email = (comment_dict.get("author_email") or "").lower()
        domain = email.split("@", 1)[1] if "@" in email else ""
        company = None
        if domain:
            try:
                tenant = _tr.get_tenant_by_domain(domain)
                if tenant:
                    company = tenant.get("name") or tenant.get("slug")
            except Exception:  # noqa: BLE001
                company = None
        return {
            "id": comment_dict.get("id"),
            "author_display": comment_dict.get("author_name") or email.split("@", 1)[0].title(),
            "author_email": email,
            "author_company": company,
            "body": comment_dict.get("body"),
            "vote_score": comment_dict.get("vote_score") or 0,
            "your_vote": comment_dict.get("your_vote") or 0,
            "created_at": comment_dict.get("created_at"),
            "replies": [_enrich(r) for r in (comment_dict.get("replies") or [])],
        }

    enriched = [_enrich(t.to_dict()) for t in threads[:limit]]
    # Total count including replies
    total = sum(1 + len(t.get("replies") or []) for t in enriched)
    return {
        "article_id": article_id,
        "count": total,
        "threads": enriched,
    }
