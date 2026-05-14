"""L7 — Belief read endpoint.

Read-only surface over the CompanyAgent belief state. Beliefs are
persisted to `data/agents/<tenant>/beliefs.json` by
`CompanyAgent.dump_to_disk()` (called by the agent when state changes;
the wiring of "auto-dump on update_belief" is a follow-up choice).

Endpoints:
  GET /api/companies/{slug}/beliefs        — current snapshot
  GET /api/companies/{slug}/beliefs/{name} — single belief by name

Both require the X-API-Key auth used by the other read endpoints; the
shape mirrors `engine.governance.company_agent.Belief.__dict__`.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_api_key
from engine.governance.company_agent import CompanyAgent

router = APIRouter(
    prefix="/api/companies",
    tags=["beliefs"],
    dependencies=[Depends(require_api_key)],
)


def _serialize_belief(belief: Any) -> dict[str, Any]:
    return {
        "name": belief.name,
        "value": belief.value,
        "confidence": belief.confidence,
        "rationale": belief.rationale,
        "actor": belief.actor,
        "updated_at": belief.updated_at,
    }


@router.get("/{slug}/beliefs")
def list_beliefs(slug: str) -> dict[str, Any]:
    """Return the current persisted belief snapshot for `slug`.

    Returns `{"tenant": slug, "beliefs": []}` when no snapshot exists
    rather than 404 — beliefs are eventually-consistent state.
    """
    agent = CompanyAgent.load_from_disk(slug)
    return {
        "tenant": slug,
        "beliefs": [_serialize_belief(b) for b in agent.beliefs.values()],
        "count": len(agent.beliefs),
    }


@router.get("/{slug}/beliefs/{name}")
def get_belief(slug: str, name: str) -> dict[str, Any]:
    """Return a single belief by name. 404 if absent."""
    agent = CompanyAgent.load_from_disk(slug)
    belief = agent.beliefs.get(name)
    if belief is None:
        raise HTTPException(
            status_code=404,
            detail=f"no belief named {name!r} for tenant {slug!r}",
        )
    return _serialize_belief(belief)
