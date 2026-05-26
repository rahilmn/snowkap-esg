"""Phase 34.5 — Article comments API.

Endpoints (all JWT-gated; identity is the `sub` claim, never anonymous):
  GET    /api/articles/{article_id}/comments  → list threaded comments
  POST   /api/articles/{article_id}/comments  → post a new comment (top-level or reply)
  DELETE /api/comments/{comment_id}           → author-only soft-delete
  POST   /api/comments/{comment_id}/vote      → cast / change / retract a vote

Mirrors the spec from the Power-of-Now prototype while preserving the
non-anonymous requirement (`author_email` is always the JWT subject).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_api_key
from api.auth_context import get_bearer_claims
from engine.index.sqlite_index import get_by_id
from engine.models import article_comments as _ac

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["article-comments"],
    dependencies=[Depends(require_api_key)],
)


class CommentCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)
    parent_id: str | None = Field(
        default=None,
        description="Top-level comment id to reply to (depth = 1 only). NULL = new top-level.",
    )


class VoteRequest(BaseModel):
    direction: int = Field(..., ge=-1, le=1, description="+1 upvote, -1 downvote, 0 retract")


def _author_identity(claims: dict[str, Any]) -> tuple[str, str]:
    """Return ``(email, display_name)`` for the comment author."""
    email = (claims.get("sub") or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=403, detail="JWT subject is not a valid email; cannot post.")
    name = (claims.get("name") or "").strip()
    if not name:
        # Fall back to the localpart of the email.
        name = email.split("@", 1)[0].title()
    return email, name


@router.get("/api/articles/{article_id}/comments")
def list_comments(
    article_id: str,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    if not get_by_id(article_id):
        raise HTTPException(status_code=404, detail=f"article not found: {article_id}")
    viewer = (claims.get("sub") or "").strip() or None
    threads = _ac.list_comments(article_id, viewer_email=viewer)
    return {
        "article_id": article_id,
        "count": sum(1 + len(t.replies) for t in threads),
        "threads": [t.to_dict() for t in threads],
    }


@router.post("/api/articles/{article_id}/comments")
def post_comment(
    article_id: str,
    req: CommentCreate,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    if not get_by_id(article_id):
        raise HTTPException(status_code=404, detail=f"article not found: {article_id}")
    email, name = _author_identity(claims)

    # Guardrail: 1-level depth only. If parent_id is supplied, it must reference
    # an existing TOP-LEVEL comment on the same article.
    if req.parent_id:
        with _ac._db_connect() as conn:  # noqa: SLF001 — internal helper reuse
            row = conn.execute(
                "SELECT article_id, parent_id FROM article_comments WHERE id = ?",
                (req.parent_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"parent comment not found: {req.parent_id}")
            parent_article = row[0] if not hasattr(row, "keys") else row["article_id"]
            parent_of_parent = row[1] if not hasattr(row, "keys") else row["parent_id"]
            if parent_article != article_id:
                raise HTTPException(status_code=422, detail="parent comment is on a different article")
            if parent_of_parent is not None:
                raise HTTPException(
                    status_code=422,
                    detail="comment threads are 1-level deep — reply to the top-level comment instead",
                )

    try:
        c = _ac.add_comment(
            article_id=article_id,
            parent_id=req.parent_id,
            author_email=email,
            author_name=name,
            body=req.body,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"comment": c.to_dict()}


@router.delete("/api/comments/{comment_id}")
def delete_comment(
    comment_id: str,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    email, _ = _author_identity(claims)
    ok = _ac.soft_delete(comment_id, requester_email=email)
    if not ok:
        raise HTTPException(
            status_code=403,
            detail="comment not found or not owned by the requester",
        )
    return {"deleted": True, "comment_id": comment_id}


@router.post("/api/comments/{comment_id}/vote")
def vote_comment(
    comment_id: str,
    req: VoteRequest,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    email, _ = _author_identity(claims)
    try:
        _ac.vote(comment_id, voter_email=email, direction=req.direction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"voted": req.direction, "comment_id": comment_id}
