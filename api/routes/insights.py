"""/api/insights and /api/companies/{slug}/insights routes."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import require_api_key
from engine.config import get_company, get_data_path
from engine.index.sqlite_index import get_by_id, query_feed

router = APIRouter(tags=["insights"], dependencies=[Depends(require_api_key)])


def _resolve_json_path(relative_path: str) -> Path:
    """Resolve a stored json_path back to an absolute filesystem path."""
    p = Path(relative_path)
    if p.is_absolute():
        return p
    # The index stores paths relative to the project root (e.g. data/outputs/...)
    project_root = get_data_path().parent
    return project_root / relative_path


@router.get("/api/companies/{slug}/insights")
def company_insights(
    slug: str,
    tier: str | None = Query(None, regex="^(HOME|SECONDARY|REJECTED)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    try:
        get_company(slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    rows = query_feed(company_slug=slug, tier=tier, limit=limit, offset=offset)
    return {"count": len(rows), "company_slug": slug, "items": rows}


@router.get("/api/insights/{article_id}")
def insight_detail(
    article_id: str,
    perspective: str | None = Query(
        None, regex="^(cfo|ceo|esg-analyst)$", description="Return perspective-specific view"
    ),
) -> dict:
    row = get_by_id(article_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Insight {article_id} not found")

    json_path = _resolve_json_path(row["json_path"])
    if not json_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Indexed file missing on disk: {row['json_path']}",
        )

    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read insight JSON: {exc}") from exc

    if perspective:
        # Return only the requested perspective view + article metadata
        perspectives = payload.get("perspectives") or {}
        view = perspectives.get(perspective)
        if not view:
            raise HTTPException(
                status_code=404,
                detail=f"Perspective '{perspective}' not available for this insight",
            )
        return {
            "article": payload.get("article"),
            "perspective": view,
            "index": row,
        }

    # Full payload by default
    return {"index": row, "payload": payload}


@router.get("/api/feed")
def global_feed(
    tier: str | None = Query(None, regex="^(HOME|SECONDARY|REJECTED)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    rows = query_feed(tier=tier, limit=limit, offset=offset)
    return {"count": len(rows), "items": rows}
