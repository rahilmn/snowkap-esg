"""Phase 34.7 — Personal Wiki API.

Endpoints (all JWT-gated; identity is the `sub` claim):
  GET    /api/me/bookmarks                    → list the caller's bookmarks (newest-first)
  POST   /api/me/bookmarks                    → add or update a bookmark for an article
  DELETE /api/me/bookmarks/{article_id}       → remove a bookmark
  PATCH  /api/me/bookmarks/{article_id}       → update note or section on an existing bookmark
  POST   /api/me/bookmarks/bulk               → idempotent bulk insert (used by client-side migration)

The article_id can be any indexed article id — we do NOT 404 on missing
article rows so the Wiki can keep historical bookmarks even if the
underlying article is archived. Identity = JWT sub claim email.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_api_key
from api.auth_context import get_bearer_claims
from engine.models import user_bookmarks as _ub

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["user-bookmarks"],
    dependencies=[Depends(require_api_key)],
)


# WIKI-3 — constrain section to the canonical set (mirrors ALLOWED_SECTIONS in
# engine/models/user_bookmarks.py) so a typo ("climte") returns 422 instead of
# being silently filed under "custom" and vanishing from filtered retrieval.
# None stays valid (server-side _normalise_section still applies) so existing
# callers don't break.
_Section = Literal["pinned", "climate", "capital", "social", "custom"]


class BookmarkCreate(BaseModel):
    article_id: str = Field(..., min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=4000)
    section: _Section | None = Field(
        default="pinned",
        description="Wiki section. One of pinned|climate|capital|social|custom (defaults to pinned).",
    )


class BookmarkPatch(BaseModel):
    note: str | None = Field(default=None, max_length=4000)
    section: _Section | None = Field(default=None)


class BulkBookmarkRequest(BaseModel):
    items: list[BookmarkCreate] = Field(
        ...,
        description="List of bookmarks to bulk-insert. Existing rows are left alone (idempotent).",
    )


def _caller_email(claims: dict[str, Any]) -> str:
    email = (claims.get("sub") or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=403, detail="JWT subject is not a valid email.")
    return email


@router.get("/api/me/bookmarks")
def list_bookmarks(
    section: str | None = None,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    email = _caller_email(claims)
    rows = _ub.list_for_user(email, section=section)
    return {
        "count": len(rows),
        "section": section,
        "bookmarks": [r.to_dict() for r in rows],
    }


@router.post("/api/me/bookmarks")
def add_bookmark(
    req: BookmarkCreate,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    email = _caller_email(claims)
    try:
        row = _ub.add(
            user_email=email,
            article_id=req.article_id,
            note=req.note,
            section=req.section,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"bookmark": row.to_dict()}


@router.delete("/api/me/bookmarks/{article_id}")
def delete_bookmark(
    article_id: str,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    email = _caller_email(claims)
    deleted = _ub.remove(email, article_id)
    return {"deleted": deleted, "article_id": article_id}


@router.patch("/api/me/bookmarks/{article_id}")
def patch_bookmark(
    article_id: str,
    req: BookmarkPatch,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    email = _caller_email(claims)
    if req.note is None and req.section is None:
        raise HTTPException(status_code=422, detail="Provide at least one of `note` or `section` to patch.")
    updated_fields: list[str] = []
    if req.note is not None:
        if _ub.update_note(email, article_id, req.note):
            updated_fields.append("note")
    if req.section is not None:
        if _ub.update_section(email, article_id, req.section):
            updated_fields.append("section")
    if not updated_fields:
        raise HTTPException(status_code=404, detail="bookmark not found")
    return {"updated": updated_fields, "article_id": article_id}


@router.post("/api/me/bookmarks/bulk")
def bulk_add_bookmarks(
    req: BulkBookmarkRequest,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    email = _caller_email(claims)
    payload = [
        {
            "article_id": item.article_id,
            "note": item.note,
            "section": item.section,
        }
        for item in req.items
    ]
    inserted = _ub.bulk_add(email, payload)
    return {"received": len(req.items), "inserted": inserted}
