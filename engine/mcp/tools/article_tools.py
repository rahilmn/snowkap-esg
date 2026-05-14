"""MCP adapter for article-list.

Defers to the existing SQLite index (`engine.index.sqlite_index.query_feed`)
so the agent surfaces the same articles the React app would.
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
