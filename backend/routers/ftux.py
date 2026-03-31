"""FTUX (First-Time User Experience) API — Module 11 (v2.0).

Endpoints for the activation window, walkthrough, and sector defaults.
FTUX state is stored in TenantConfig.custom_fields["ftux"].
"""

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.core.dependencies import TenantContext, get_tenant_context

logger = structlog.get_logger()
router = APIRouter()


class FTUXStateResponse(BaseModel):
    is_active: bool
    completed_steps: list[str]
    current_step: int
    total_steps: int
    estimated_minutes: int
    completed_at: str | None = None


class StepCompleteRequest(BaseModel):
    step_id: str


async def _get_or_create_config(ctx: TenantContext):
    """Load or create TenantConfig for the current tenant."""
    from sqlalchemy import select
    from backend.models.tenant import TenantConfig

    result = await ctx.db.execute(
        select(TenantConfig).where(TenantConfig.tenant_id == ctx.tenant_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        config = TenantConfig(tenant_id=ctx.tenant_id)
        ctx.db.add(config)
        await ctx.db.flush()
    return config


@router.get("/state", response_model=FTUXStateResponse)
async def get_ftux_state(
    ctx: TenantContext = Depends(get_tenant_context),
) -> FTUXStateResponse:
    """Get current FTUX progress for this tenant."""
    from backend.services.ftux_service import get_ftux_state as _get_state

    config = await _get_or_create_config(ctx)
    # FTUX state stored in custom_fields["ftux"]
    custom = config.custom_fields or {}
    state = _get_state(custom)
    return FTUXStateResponse(**state)


@router.get("/walkthrough")
async def get_walkthrough(
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Get walkthrough steps and educational content."""
    from backend.services.ftux_service import get_walkthrough, get_educational_content

    return {
        "steps": get_walkthrough(),
        "educational_content": get_educational_content(),
    }


@router.get("/sector-defaults")
async def get_sector_defaults(
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Get pre-populated sector-default stories for FTUX."""
    from sqlalchemy import select
    from backend.models.company import Company
    from backend.services.ftux_service import get_sector_defaults

    comp_result = await ctx.db.execute(
        select(Company).where(Company.tenant_id == ctx.tenant_id).limit(1)
    )
    company = comp_result.scalar_one_or_none()
    industry = company.industry if company else None

    return {"stories": get_sector_defaults(industry)}


@router.post("/complete-step")
async def complete_step(
    req: StepCompleteRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Mark a walkthrough step as complete."""
    from backend.services.ftux_service import mark_step_complete

    config = await _get_or_create_config(ctx)
    custom = config.custom_fields or {}
    config.custom_fields = mark_step_complete(custom, req.step_id)
    await ctx.db.flush()

    return {"status": "ok", "step": req.step_id}


@router.post("/skip")
async def skip_ftux(
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Skip FTUX entirely."""
    from backend.services.ftux_service import mark_ftux_complete

    config = await _get_or_create_config(ctx)
    custom = config.custom_fields or {}
    config.custom_fields = mark_ftux_complete(custom)
    await ctx.db.flush()

    return {"status": "skipped"}
