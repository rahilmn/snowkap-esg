"""HTTP surface for autoresearcher experiment ledger + leaderboard.

  GET  /api/autoresearcher/experiments?tier=&limit=
  GET  /api/autoresearcher/leaderboard?tier=&top_n=
  POST /api/autoresearcher/run   (manage_drip_campaigns; budget + seed)

Read endpoints use the X-API-Key gate. The POST endpoint additionally
requires `manage_drip_campaigns` since starting a Tier-0 run can
materially affect the system.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_api_key
from api.auth_context import require_bearer_permission
from engine.autoresearcher.ledger import leaderboard as ledger_leaderboard
from engine.autoresearcher.ledger import read_ledger

router = APIRouter(
    prefix="/api/autoresearcher",
    tags=["autoresearcher"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/experiments")
def list_experiments(tier: str = "system", limit: int = 50) -> dict[str, Any]:
    """Recent experiments for one tier, newest-first."""
    if tier not in ("system", "tenant", "user"):
        raise HTTPException(status_code=422, detail=f"unknown tier {tier!r}")
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be in [1, 500]")
    all_records = list(read_ledger(tier))
    # Newest first
    all_records.reverse()
    return {
        "tier": tier,
        "count": len(all_records),
        "experiments": all_records[:limit],
    }


@router.get("/leaderboard")
def get_leaderboard(tier: str = "system", top_n: int = 20) -> dict[str, Any]:
    """Top-N kept experiments by metric_delta descending."""
    if tier not in ("system", "tenant", "user"):
        raise HTTPException(status_code=422, detail=f"unknown tier {tier!r}")
    if top_n < 1 or top_n > 100:
        raise HTTPException(status_code=422, detail="top_n must be in [1, 100]")
    top = ledger_leaderboard(tier, top_n=top_n)
    return {"tier": tier, "count": len(top), "entries": top}


class RunRequest(BaseModel):
    tier: str = Field(default="system", pattern="^(system|tenant|user)$")
    tenant_slug: str | None = Field(default=None, max_length=100)
    user_id: str | None = Field(default=None, max_length=200)
    budget: int = Field(default=20, ge=1, le=2000)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    keep_threshold: float = Field(default=0.02, ge=-1.0, le=1.0)
    min_age_days: int = Field(default=0, ge=0, le=365)


@router.post(
    "/run",
    dependencies=[Depends(require_bearer_permission("manage_drip_campaigns"))],
)
def run_session(body: RunRequest) -> dict[str, Any]:
    """Launch one autoresearcher session.

    Tier-0 is implemented. Tier-1 and Tier-2 are stubs that return
    a clear message — wiring follows in future sessions.
    """
    if body.tier == "tenant":
        if not body.tenant_slug:
            raise HTTPException(status_code=422, detail="tenant_slug required for tier=tenant")
        from engine.autoresearcher.tier1.runner import run_tier1
        result = run_tier1(
            tenant_slug=body.tenant_slug,
            budget=body.budget,
            seed=body.seed,
            keep_threshold=body.keep_threshold,
            min_age_days=body.min_age_days,
        )
        return result.summary()

    if body.tier == "user":
        if not body.user_id:
            raise HTTPException(status_code=422, detail="user_id required for tier=user")
        from engine.autoresearcher.tier2.runner import run_tier2
        result = run_tier2(
            user_id=body.user_id,
            budget=body.budget,
            seed=body.seed,
            keep_threshold=body.keep_threshold,
        )
        return result.summary()

    from engine.autoresearcher.tier0.runner import run_tier0
    result = run_tier0(
        budget=body.budget,
        seed=body.seed,
        keep_threshold=body.keep_threshold,
        min_age_days=body.min_age_days,
    )
    return result.summary()
