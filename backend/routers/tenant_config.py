"""Tenant config API — workflow stages, custom fields, business rules.

Per MASTER_BUILD_PLAN Phase 2B:
- Tenant config API: workflow stages, custom fields, business rules (JSONB)
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from backend.core.dependencies import TenantContext, get_tenant_context
from backend.core.redis import CACHE_TTL_CONFIG, cache_delete, cache_get, cache_set
from backend.models.tenant import TenantConfig

logger = structlog.get_logger()
router = APIRouter()


class TenantConfigResponse(BaseModel):
    tenant_id: str
    workflow_stages: dict | None
    custom_fields: dict | None
    business_rules: dict | None
    notification_settings: dict | None
    mirofish_config: dict | None


class TenantConfigUpdate(BaseModel):
    workflow_stages: dict | None = None
    custom_fields: dict | None = None
    business_rules: dict | None = None
    notification_settings: dict | None = None
    mirofish_config: dict | None = None


@router.get("/", response_model=TenantConfigResponse)
async def get_config(
    ctx: TenantContext = Depends(get_tenant_context),
) -> TenantConfigResponse:
    """Get tenant configuration — cached with 5min TTL."""
    # Check Redis cache first
    cached = await cache_get(ctx.tenant_id, "config", "main")
    if cached:
        return TenantConfigResponse(**cached)

    result = await ctx.db.execute(
        select(TenantConfig).where(TenantConfig.tenant_id == ctx.tenant_id)
    )
    config = result.scalar_one_or_none()

    if not config:
        # Create default config
        config = TenantConfig(tenant_id=ctx.tenant_id)
        ctx.db.add(config)
        await ctx.db.flush()

    response = TenantConfigResponse(
        tenant_id=ctx.tenant_id,
        workflow_stages=config.workflow_stages,
        custom_fields=config.custom_fields,
        business_rules=config.business_rules,
        notification_settings=config.notification_settings,
        mirofish_config=config.mirofish_config,
    )

    # Cache for 5 minutes
    await cache_set(ctx.tenant_id, "config", "main", response.model_dump(), ttl=CACHE_TTL_CONFIG)
    return response


@router.patch("/", response_model=TenantConfigResponse)
async def update_config(
    updates: TenantConfigUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> TenantConfigResponse:
    """Update tenant configuration — admin only."""
    if "manage_tenant" not in ctx.user.permissions and "platform_admin" not in ctx.user.permissions:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No permission to manage tenant config")

    result = await ctx.db.execute(
        select(TenantConfig).where(TenantConfig.tenant_id == ctx.tenant_id)
    )
    config = result.scalar_one_or_none()

    if not config:
        config = TenantConfig(tenant_id=ctx.tenant_id)
        ctx.db.add(config)

    # Apply partial updates
    if updates.workflow_stages is not None:
        config.workflow_stages = updates.workflow_stages
    if updates.custom_fields is not None:
        config.custom_fields = updates.custom_fields
    if updates.business_rules is not None:
        config.business_rules = updates.business_rules
    if updates.notification_settings is not None:
        config.notification_settings = updates.notification_settings
    if updates.mirofish_config is not None:
        config.mirofish_config = updates.mirofish_config

    await ctx.db.flush()

    # Invalidate cache
    await cache_delete(ctx.tenant_id, "config", "main")

    logger.info("tenant_config_updated", tenant_id=ctx.tenant_id, user_id=ctx.user.user_id)

    return TenantConfigResponse(
        tenant_id=ctx.tenant_id,
        workflow_stages=config.workflow_stages,
        custom_fields=config.custom_fields,
        business_rules=config.business_rules,
        notification_settings=config.notification_settings,
        mirofish_config=config.mirofish_config,
    )
