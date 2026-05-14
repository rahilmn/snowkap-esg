"""MCP adapters for wiki-search / wiki-related / wiki-page.

Reads only — no audit-trigger gate fires.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.wiki.index import WikiIndex
from engine.wiki.links import compute_backlinks


def _wiki_root(base_data: Path) -> Path:
    """Resolve the wiki root relative to the data dir.

    Layout convention: `wiki/` sits next to `data/` at repo root.
    """
    candidates = [base_data.parent / "wiki", base_data / "wiki"]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def handle_wiki_search(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    wiki_root = _wiki_root(base_data)
    if not wiki_root.exists():
        return {"hits": [], "wiki_root_missing": True}
    idx = WikiIndex.build(wiki_root)
    hits = idx.search(
        query=payload["q"],
        tier=payload.get("tier"),
        tenant_slug=payload.get("tenant"),
        user_slug=payload.get("user"),
        top_k=payload.get("top_k", 10),
    )
    return {
        "hits": [
            {
                "path": str(h.path.relative_to(wiki_root)),
                "tier": h.tier,
                "score": round(h.score, 4),
            }
            for h in hits
        ],
    }


def handle_wiki_related(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    wiki_root = _wiki_root(base_data)
    target = (wiki_root / payload["path"]).resolve()
    backlinks_map = compute_backlinks(wiki_root)
    rel_backlinks = sorted(
        str(src.relative_to(wiki_root))
        for src in backlinks_map.get(target, set())
    )
    return {"path": payload["path"], "backlinks": rel_backlinks}


def handle_wiki_page(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    wiki_root = _wiki_root(base_data)
    target = wiki_root / payload["path"]
    if not target.suffix:
        target = target.with_suffix(".md")
    if not target.exists() or not target.is_file():
        return {"path": payload["path"], "found": False, "content": ""}
    text = target.read_text(encoding="utf-8")
    return {"path": payload["path"], "found": True, "content": text}
