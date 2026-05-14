"""W1.8 — Wiki HTTP surface.

Endpoints:
  GET /api/wiki/search?q=<query>&tier=<system|tenant|user>&tenant=<slug>
      → BM25 search across tiers
  GET /api/wiki/related?path=<wiki-relative-path>
      → backlink list for a page (what links here)
  GET /api/wiki/page?path=<wiki-relative-path>
      → raw markdown content of a wiki page

All read-only. Auth gated by the standard X-API-Key header.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import require_api_key
from engine.wiki.index import WikiIndex
from engine.wiki.links import compute_backlinks
from engine.wiki.paths import wiki_root


router = APIRouter(
    prefix="/api/wiki",
    tags=["wiki"],
    dependencies=[Depends(require_api_key)],
)


def _wiki_root() -> Path:
    """Resolve the wiki root for this server process. Cached per-process
    in production via the module-level constant in paths.py."""
    return wiki_root()


def _safe_relative_path(rel: str, root: Path) -> Path:
    """Resolve a user-supplied wiki path safely (no directory traversal)."""
    # Reject absolute paths + anything trying to escape
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        raise HTTPException(status_code=400, detail="Invalid path")
    target = (root / rel).resolve()
    # Ensure target stays inside wiki root
    try:
        target.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Path escapes wiki root")
    return target


@router.get("/search")
def search(
    q: str = Query(..., min_length=1, max_length=200),
    tier: str | None = None,
    tenant: str | None = None,
    user: str | None = None,
    top_k: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """BM25 search across the wiki tiers.

    `tier` filters to one of system|tenant|user. When tier='tenant',
    `tenant` (slug) further narrows; when tier='user', `user` (slug)
    further narrows.
    """
    root = _wiki_root()
    if not root.exists():
        return {"count": 0, "hits": [], "wiki_root_missing": True}

    idx = WikiIndex.build(root)
    hits = idx.search(
        q,
        tier=tier,  # type: ignore[arg-type] — Pydantic validates the literal
        tenant_slug=tenant,
        user_slug=user,
        top_k=top_k,
    )
    return {
        "count": len(hits),
        "hits": [
            {
                "path": str(h.path.relative_to(root)).replace("\\", "/"),
                "score": round(h.score, 4),
                "tier": h.tier,
            }
            for h in hits
        ],
    }


@router.get("/related")
def related(path: str = Query(..., min_length=1, max_length=512)) -> dict[str, Any]:
    """Return the backlinks for a single wiki page.

    Computes the full backlink graph (cheap for <10k pages) and returns
    the entry for the requested page.
    """
    root = _wiki_root()
    if not root.exists():
        return {"path": path, "backlinks": [], "wiki_root_missing": True}
    target = _safe_relative_path(path, root)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Page not found: {path}")

    backlinks_map = compute_backlinks(root)
    backlinks = backlinks_map.get(target, set())
    return {
        "path": path,
        "backlinks": sorted(
            str(b.relative_to(root)).replace("\\", "/")
            for b in backlinks
        ),
    }


@router.get("/page")
def page(path: str = Query(..., min_length=1, max_length=512)) -> dict[str, Any]:
    """Return the raw markdown content of a wiki page."""
    root = _wiki_root()
    if not root.exists():
        raise HTTPException(status_code=404, detail="Wiki root not built yet")
    target = _safe_relative_path(path, root)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"Page not found: {path}")
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Read failed: {exc}") from exc
    return {"path": path, "content": content}
