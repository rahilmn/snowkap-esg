"""Phase C — Per-tool handler registry for the MCP server.

Each handler is a thin adapter that calls into an existing Snowkap
module. **No business logic lives here** — these are pure routers
that translate a validated `dict` payload to the underlying call
shape and back.

`build_default_registry(base_data)` returns the full handler dict,
which `engine.mcp.server.build_server` registers in one shot.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from engine.mcp.tools.advisor_tools import (
    handle_advisor_queue,
    handle_advisor_resolve,
)
from engine.mcp.tools.agent_tools import (
    handle_agent_beliefs_get,
    handle_agent_state_get,
)
from engine.mcp.tools.article_tools import handle_article_list, handle_article_comments
from engine.mcp.tools.forum_tools import handle_forum_thread
from engine.mcp.tools.wiki_bookmarks_tools import handle_bookmarks_recall
from engine.mcp.tools.autoresearcher_tools import (
    handle_autoresearcher_experiments,
    handle_autoresearcher_leaderboard,
)
from engine.mcp.tools.intelligence_tools import (
    handle_intelligence_competitors,
    handle_intelligence_forecast,
)
from engine.mcp.tools.memory_tools import (
    handle_memory_list,
    handle_memory_recall,
)
from engine.mcp.tools.wiki_tools import (
    handle_wiki_page,
    handle_wiki_related,
    handle_wiki_search,
)


def build_default_registry(
    base_data: Path,
) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """Return the {tool_name: handler} mapping for the default MCP catalog.

    Handlers are closures over `base_data` so tests can point them at
    a tmp dir.
    """
    return {
        "wiki-search":               lambda p: handle_wiki_search(p, base_data),
        "wiki-related":              lambda p: handle_wiki_related(p, base_data),
        "wiki-page":                 lambda p: handle_wiki_page(p, base_data),
        "intelligence-competitors":  lambda p: handle_intelligence_competitors(p, base_data),
        "intelligence-forecast":     lambda p: handle_intelligence_forecast(p, base_data),
        "advisor-queue":             lambda p: handle_advisor_queue(p, base_data),
        "advisor-resolve":           lambda p: handle_advisor_resolve(p, base_data),
        "autoresearcher-experiments":lambda p: handle_autoresearcher_experiments(p, base_data),
        "autoresearcher-leaderboard":lambda p: handle_autoresearcher_leaderboard(p, base_data),
        "agent-beliefs-get":         lambda p: handle_agent_beliefs_get(p, base_data),
        "agent-state-get":           lambda p: handle_agent_state_get(p, base_data),
        "article-list":              lambda p: handle_article_list(p, base_data),
        "article-comments":          lambda p: handle_article_comments(p, base_data),
        "forum-thread":              lambda p: handle_forum_thread(p, base_data),
        "bookmarks-recall":          lambda p: handle_bookmarks_recall(p, base_data),
        "memory-recall":             lambda p: handle_memory_recall(p, base_data),
        "memory-list":               lambda p: handle_memory_list(p, base_data),
    }
