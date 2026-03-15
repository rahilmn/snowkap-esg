"""Campaigns router — newsletter campaigns."""

import structlog
from fastapi import APIRouter, Depends

from backend.core.dependencies import TenantContext, get_tenant_context

logger = structlog.get_logger()
router = APIRouter()


@router.get("/")
async def list_campaigns(
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """List campaigns for this tenant."""
    # TODO: Phase 2 — migrate campaign logic from Express
    return {"campaigns": [], "total": 0}
