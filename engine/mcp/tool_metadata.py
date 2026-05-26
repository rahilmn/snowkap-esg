"""Per-tool input schemas + annotations (MCP-spec compatible).

Each entry: input model (Pydantic), annotations dict with the standard
readOnlyHint / destructiveHint / idempotentHint / openWorldHint flags.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------- Input models -----------------------------------------------------


class WikiSearchInput(BaseModel):
    q: str = Field(..., min_length=1, max_length=200)
    tier: str | None = None       # "system" | "tenant" | "user"
    tenant: str | None = None
    user: str | None = None
    top_k: int = Field(default=10, ge=1, le=50)


class WikiRelatedInput(BaseModel):
    path: str = Field(..., min_length=1, max_length=512)


class WikiPageInput(BaseModel):
    path: str = Field(..., min_length=1, max_length=512)


class TenantInput(BaseModel):
    tenant: str = Field(..., min_length=1, max_length=100)


class ArticleListInput(BaseModel):
    tenant: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ArticleCommentsInput(BaseModel):
    """POW-5b — Fetch the threaded discussion on an article."""
    article_id: str = Field(min_length=1, max_length=128)
    viewer_email: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ForumThreadInput(BaseModel):
    """Forum v1.1 — Fetch one forum thread + its replies with author-company
    attribution. Mirrors ArticleCommentsInput so the LLM gateway can
    route them through the same dispatch path.
    """
    thread_id: str = Field(min_length=1, max_length=128)
    viewer_email: str | None = None


class BookmarksRecallInput(BaseModel):
    """Wiki v1.1 — Fetch the caller's personal bookmark library, enriched
    with each article's title + criticality_band. The dispatch layer
    enforces that `viewer_email` matches the JWT sub claim so this tool
    can never leak across users.
    """
    viewer_email: str = Field(min_length=3, max_length=256)
    section: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class AdvisorQueueInput(BaseModel):
    tenant: str | None = None


class AdvisorResolveInput(BaseModel):
    event_id: str
    resolution: str = Field(..., pattern="^(approve|reject)$")
    rationale: str = ""


class AutoresearcherInput(BaseModel):
    tier: str = "system"
    limit: int = Field(default=20, ge=1, le=200)


class AgentTenantInput(BaseModel):
    tenant: str = Field(..., min_length=1, max_length=100)


class MemoryRecallInput(BaseModel):
    tenant: str
    user: str
    query: str
    top_n: int = Field(default=8, ge=1, le=50)


class MemoryListInput(BaseModel):
    tenant: str
    user: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


# ---------- Registry -------------------------------------------------------


TOOL_METADATA: dict[str, dict[str, Any]] = {
    "wiki-search":               {"input_model": WikiSearchInput,    "annotations": {"readOnlyHint": True}},
    "wiki-related":              {"input_model": WikiRelatedInput,   "annotations": {"readOnlyHint": True}},
    "wiki-page":                 {"input_model": WikiPageInput,      "annotations": {"readOnlyHint": True}},
    "intelligence-competitors":  {"input_model": TenantInput,        "annotations": {"readOnlyHint": True}},
    "intelligence-forecast":     {"input_model": TenantInput,        "annotations": {"readOnlyHint": True}},
    "advisor-queue":             {"input_model": AdvisorQueueInput,  "annotations": {"readOnlyHint": True}},
    "advisor-resolve":           {"input_model": AdvisorResolveInput, "annotations": {"destructiveHint": True, "idempotentHint": False}},
    "autoresearcher-experiments":{"input_model": AutoresearcherInput, "annotations": {"readOnlyHint": True}},
    "autoresearcher-leaderboard":{"input_model": AutoresearcherInput, "annotations": {"readOnlyHint": True}},
    "agent-beliefs-get":         {"input_model": AgentTenantInput,   "annotations": {"readOnlyHint": True}},
    "agent-state-get":           {"input_model": AgentTenantInput,   "annotations": {"readOnlyHint": True}},
    "article-list":              {"input_model": ArticleListInput,   "annotations": {"readOnlyHint": True}},
    "article-comments":          {"input_model": ArticleCommentsInput, "annotations": {"readOnlyHint": True}},
    "forum-thread":              {"input_model": ForumThreadInput,   "annotations": {"readOnlyHint": True}},
    "bookmarks-recall":          {"input_model": BookmarksRecallInput, "annotations": {"readOnlyHint": True}},
    "memory-recall":             {"input_model": MemoryRecallInput,  "annotations": {"readOnlyHint": True}},
    "memory-list":               {"input_model": MemoryListInput,    "annotations": {"readOnlyHint": True}},
}


def input_schema_for(tool_name: str) -> dict[str, Any]:
    """Return JSON Schema for a tool's input. Empty dict for unknown tools."""
    entry = TOOL_METADATA.get(tool_name)
    if not entry:
        return {}
    model = entry["input_model"]
    return model.model_json_schema()


def annotations_for(tool_name: str) -> dict[str, Any]:
    entry = TOOL_METADATA.get(tool_name)
    if not entry:
        return {}
    return dict(entry["annotations"])


def is_destructive(tool_name: str) -> bool:
    return bool(annotations_for(tool_name).get("destructiveHint"))
