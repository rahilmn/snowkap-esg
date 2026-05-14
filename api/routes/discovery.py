"""Phase 24 (W2) — admin self-evolving ontology review surface.

Closes the Phase 19 loop: Tier-1 candidates auto-promote, but Tier-2/3
sit in ``data/ontology/discovery_staging.json`` indefinitely with no
human review path. This router gives admins a structured way to:

  1. ``GET  /api/admin/discovery/staged`` — enumerate pending candidates
     grouped by category, with confidence + provenance for each.
  2. ``POST /api/admin/discovery/decide`` — apply a promote/reject/defer
     decision with required Toulmin justification (for reject + defer).
  3. ``GET  /api/admin/discovery/history`` — read the most recent N
     entries from ``data/audit/promotion_log.jsonl`` so analysts can
     audit their own past decisions.

Auth: gated by ``manage_drip_campaigns`` (super-admin only) — same as
the onboarding endpoint. Self-evolving ontology mutations affect every
tenant on the instance, so the gate is intentionally restrictive.

This router is additive — it does NOT replace the legacy
``/api/discovery/*`` endpoints in ``legacy_adapter.py`` (kept for
back-compat with the Phase 19 audit dashboard). New work writes here.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_auth
from api.auth_context import require_bearer_permission

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/discovery",
    tags=["admin-discovery"],
    dependencies=[
        Depends(require_auth),
        Depends(require_bearer_permission("manage_drip_campaigns")),
    ],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ToulminBlock(BaseModel):
    """Required justification for reject + defer decisions.

    Mirrors :class:`engine.audit.ToulminDict` — claim/grounds/warrant
    are required, qualifier/rebuttal are optional. The promotion_log
    writer enforces non-empty grounds + warrant on the persisted record.
    """
    claim: str = Field(..., min_length=4, max_length=500)
    grounds: list[str] = Field(..., min_length=1, max_length=10)
    warrant: str = Field(..., min_length=4, max_length=500)
    qualifier: str | None = Field(default=None, max_length=200)
    rebuttal: str | None = Field(default=None, max_length=500)


class DecideRequest(BaseModel):
    candidate_id: str = Field(..., pattern=r"^[a-z_]+:[a-zA-Z0-9_\-]+$",
                              description="'category:slug' (e.g. 'entity:tata_power')")
    decision: Literal["promote", "reject", "defer"]
    toulmin: ToulminBlock | None = None  # required for reject + defer


class DecideResponse(BaseModel):
    ok: bool
    message: str
    category: str | None = None
    slug: str | None = None
    decision: str | None = None
    triples_added: int = 0
    new_status: str | None = None


# ---------------------------------------------------------------------------
# GET /api/admin/discovery/staged
# ---------------------------------------------------------------------------


@router.get("/staged")
def get_staged(
    category: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return staged (pending) discovery candidates.

    Optional ``category`` filter scopes to one of: entity, theme, event,
    edge, weight, stakeholder, framework. Results are sorted by
    confidence DESC, then article_count DESC so the highest-quality
    candidates surface first.

    Each candidate carries:
      * label + slug + category
      * confidence (0.0-1.0)
      * article_count + source_count + company_count
      * article_ids[:5] + sources[:5] (provenance for inspection)
      * data (category-specific payload — entity_type, pillar, etc.)
      * candidate_id ('category:slug' — pass back to /decide)
    """
    from engine.ontology.discovery.candidates import (
        STATUS_PENDING,
        get_buffer,
    )

    buf = get_buffer()
    candidates = buf.get_all(category=category, status=STATUS_PENDING)

    # Sort: highest confidence first, then most-evidenced
    candidates.sort(key=lambda c: (-c.confidence, -c.article_count))
    candidates = candidates[: max(0, min(limit, 500))]

    payload: list[dict[str, Any]] = []
    for c in candidates:
        d = c.to_dict()
        # Trim provenance lists to keep response payload lean
        d["article_ids"] = (d.get("article_ids") or [])[:5]
        d["sources"] = (d.get("sources") or [])[:5]
        d["companies"] = (d.get("companies") or [])[:5]
        d["candidate_id"] = f"{c.category}:{c.slug}"
        payload.append(d)

    # Group counts for the UI tabs
    by_category: dict[str, int] = {}
    for c in candidates:
        by_category[c.category] = by_category.get(c.category, 0) + 1

    return {
        "count": len(payload),
        "by_category": by_category,
        "candidates": payload,
    }


# ---------------------------------------------------------------------------
# POST /api/admin/discovery/decide
# ---------------------------------------------------------------------------


@router.post("/decide", response_model=DecideResponse)
def decide(req: DecideRequest, request_user_id: str | None = None) -> DecideResponse:
    """Apply a promote/reject/defer decision to a staged candidate.

    Reject + defer require a Toulmin block (claim/grounds/warrant +
    optional qualifier/rebuttal) — the analyst must articulate WHY they
    reject or defer. Promote does not require Toulmin (the candidate's
    confidence + article count is the implicit warrant), but admins are
    encouraged to attach one for audit-quality decisions.

    Every decision writes to ``data/audit/promotion_log.jsonl`` via
    :func:`engine.audit.append_promotion` AND to the legacy
    ``data/ontology/discovery_audit.jsonl`` (Phase 19 back-compat).
    """
    from engine.ontology.discovery.promoter import manual_decide

    toulmin_dict = req.toulmin.model_dump(exclude_none=True) if req.toulmin else None

    result = manual_decide(
        req.candidate_id,
        req.decision,
        toulmin=toulmin_dict,
        user_id=request_user_id,
    )

    if not result.ok:
        # Convert validation-style failures into 4xx for the UI
        if "not found" in result.message:
            raise HTTPException(status_code=404, detail=result.message)
        raise HTTPException(status_code=400, detail=result.message)

    return DecideResponse(**result.to_dict())


# ---------------------------------------------------------------------------
# GET /api/admin/discovery/history
# ---------------------------------------------------------------------------


@router.get("/history")
def get_history(limit: int = 50, decision: str | None = None) -> dict[str, Any]:
    """Read the most recent N entries from the Phase 24 promotion log.

    Optional ``decision`` filter: ``promote``, ``reject``, ``defer``.
    Results are returned newest-first (file is append-only, so we read
    all then reverse — fine for the typical < 10k entry log size).
    """
    from engine import audit as _audit

    entries = list(_audit.read_promotion_log())
    if decision:
        entries = [e for e in entries if e.get("decision") == decision]

    entries.reverse()  # newest first
    entries = entries[: max(0, min(limit, 500))]

    return {
        "count": len(entries),
        "entries": entries,
    }
