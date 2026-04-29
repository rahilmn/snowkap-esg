"""/api/insights and /api/companies/{slug}/insights routes."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from api.auth import require_api_key
from engine.config import get_company, get_data_path
from engine.index.sqlite_index import get_by_id, query_feed

logger = logging.getLogger(__name__)

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


def _trigger_background_regenerate(article_id: str, slug: str) -> None:
    """Phase 13 B4: schedule on-demand re-enrichment when an indexed JSON
    file is missing or malformed. Best-effort — failures here are logged
    but never block the user-facing 202 response."""
    try:
        from engine.analysis.on_demand import enrich_on_demand
        enrich_on_demand(article_id=article_id, company_slug=slug, force=True)
    except Exception:  # noqa: BLE001 — background task, log + swallow
        logger.exception("background regenerate failed for %s/%s", slug, article_id)


# response_model=None: this endpoint returns either dict (200) or
# JSONResponse (202 regenerating). FastAPI can't auto-derive a response
# model from the union, so we suppress schema generation here.
@router.get("/api/insights/{article_id}", response_model=None)
def insight_detail(
    article_id: str,
    background_tasks: BackgroundTasks,
    perspective: str | None = Query(
        None, regex="^(cfo|ceo|esg-analyst)$", description="Return perspective-specific view"
    ),
):
    row = get_by_id(article_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Insight {article_id} not found")

    json_path = _resolve_json_path(row["json_path"])

    # Phase 13 B4: graceful fallback when the indexed file is missing or
    # malformed (truncated mid-write, stale path, encoding error). Instead
    # of returning a raw 500 with stack trace (which kills demo trust),
    # return HTTP 202 + queue a background re-enrichment job. The UI can
    # poll the article-status endpoint and re-fetch when ready.
    def _regen_response(reason: str) -> JSONResponse:
        slug = (row.get("company_slug") or "").strip()
        if slug:
            background_tasks.add_task(_trigger_background_regenerate, article_id, slug)
        logger.warning(
            "insight_detail: serving 202 regenerating for %s (reason=%s)",
            article_id, reason,
        )
        return JSONResponse(
            status_code=202,
            content={
                "state": "regenerating",
                "article_id": article_id,
                "reason": reason,
                "retry_after_seconds": 30,
            },
            headers={"Retry-After": "30"},
        )

    if not json_path.exists():
        return _regen_response("file_missing_on_disk")

    try:
        raw = json_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("insight_detail: read failed for %s: %s", json_path, exc)
        return _regen_response(f"read_failed:{type(exc).__name__}")
    except UnicodeDecodeError as exc:
        logger.error("insight_detail: encoding error for %s: %s", json_path, exc)
        return _regen_response("encoding_error")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(
            "insight_detail: malformed JSON for %s at line %d col %d: %s",
            json_path, exc.lineno, exc.colno, exc.msg,
        )
        return _regen_response("malformed_json")

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
