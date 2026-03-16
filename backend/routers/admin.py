"""Admin router — platform admin console.

Per MASTER_BUILD_PLAN Phase 7:
- Tenant CRUD (list, get, update, deactivate)
- User management per tenant
- Impersonate user (platform admin only)
- Usage analytics
- Role/permission management
"""

from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.core.dependencies import TenantContext, get_tenant_context
from backend.core.permissions import (
    Permission,
    Role,
    get_permissions_for_role,
    require_permission,
)
from backend.core.security import create_jwt_token
from backend.models.news import Article
from backend.models.prediction import PredictionReport
from backend.models.tenant import Tenant, TenantConfig, TenantMembership
from backend.models.user import User

logger = structlog.get_logger()
router = APIRouter()


# --- Schemas ---

class TenantSummary(BaseModel):
    id: str
    name: str
    domain: str
    industry: str | None
    is_active: bool
    user_count: int
    created_at: datetime | None = None


class TenantDetail(TenantSummary):
    sasb_category: str | None
    sustainability_query: str | None
    general_query: str | None
    config: dict | None = None


class TenantUpdateRequest(BaseModel):
    name: str | None = None
    industry: str | None = None
    sasb_category: str | None = None
    is_active: bool | None = None


class UserSummary(BaseModel):
    id: str
    email: str
    name: str | None
    designation: str | None
    domain: str
    role: str | None
    permissions: list[str]
    is_active: bool
    last_login: datetime | None = None


class UpdateUserRoleRequest(BaseModel):
    role: str
    custom_permissions: list[str] | None = None


class ImpersonateRequest(BaseModel):
    user_id: str
    tenant_id: str


class ImpersonateResponse(BaseModel):
    token: str
    user_id: str
    tenant_id: str
    message: str


class UsageStats(BaseModel):
    total_tenants: int
    active_tenants: int
    total_users: int
    active_users_30d: int
    total_articles: int
    total_predictions: int
    tenants_by_industry: dict[str, int]


class TenantUsageDetail(BaseModel):
    tenant_id: str
    tenant_name: str
    user_count: int
    article_count: int
    prediction_count: int
    last_activity: datetime | None = None


# --- Platform Admin Endpoints (Snowkap staff) ---

@router.get(
    "/tenants",
    response_model=list[TenantSummary],
    dependencies=[Depends(require_permission(Permission.VIEW_ALL_TENANTS))],
)
async def list_all_tenants(
    is_active: bool | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[TenantSummary]:
    """List all tenants — platform admin only."""
    query = select(Tenant)
    if is_active is not None:
        query = query.where(Tenant.is_active == is_active)
    query = query.order_by(Tenant.created_at.desc()).limit(limit).offset(offset)

    result = await ctx.db.execute(query)
    tenants = result.scalars().all()

    summaries = []
    for t in tenants:
        count_result = await ctx.db.execute(
            select(func.count(TenantMembership.id)).where(
                TenantMembership.tenant_id == t.id,
                TenantMembership.is_active == True,
            )
        )
        user_count = count_result.scalar() or 0
        summaries.append(
            TenantSummary(
                id=t.id, name=t.name, domain=t.domain,
                industry=t.industry, is_active=t.is_active,
                user_count=user_count, created_at=t.created_at,
            )
        )
    return summaries


@router.get(
    "/tenants/{tenant_id}",
    response_model=TenantDetail,
    dependencies=[Depends(require_permission(Permission.VIEW_ALL_TENANTS))],
)
async def get_tenant_detail(
    tenant_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
) -> TenantDetail:
    """Get detailed tenant info — platform admin only."""
    result = await ctx.db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    count_result = await ctx.db.execute(
        select(func.count(TenantMembership.id)).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.is_active == True,
        )
    )
    user_count = count_result.scalar() or 0

    config_result = await ctx.db.execute(
        select(TenantConfig).where(TenantConfig.tenant_id == tenant_id)
    )
    config = config_result.scalar_one_or_none()

    return TenantDetail(
        id=tenant.id, name=tenant.name, domain=tenant.domain,
        industry=tenant.industry, sasb_category=tenant.sasb_category,
        is_active=tenant.is_active, user_count=user_count,
        sustainability_query=tenant.sustainability_query,
        general_query=tenant.general_query,
        created_at=tenant.created_at,
        config={
            "workflow_stages": config.workflow_stages,
            "mirofish_config": config.mirofish_config,
        } if config else None,
    )


@router.patch(
    "/tenants/{tenant_id}",
    response_model=TenantDetail,
    dependencies=[Depends(require_permission(Permission.PLATFORM_ADMIN))],
)
async def update_tenant(
    tenant_id: str,
    req: TenantUpdateRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> TenantDetail:
    """Update tenant details — platform admin only."""
    result = await ctx.db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    if req.name is not None:
        tenant.name = req.name
    if req.industry is not None:
        tenant.industry = req.industry
    if req.sasb_category is not None:
        tenant.sasb_category = req.sasb_category
    if req.is_active is not None:
        tenant.is_active = req.is_active

    await ctx.db.flush()
    logger.info("tenant_updated", tenant_id=tenant_id, updates=req.model_dump(exclude_none=True))

    return await get_tenant_detail(tenant_id, ctx)


@router.post(
    "/impersonate",
    response_model=ImpersonateResponse,
    dependencies=[Depends(require_permission(Permission.IMPERSONATE_USER))],
)
async def impersonate_user(
    req: ImpersonateRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> ImpersonateResponse:
    """Impersonate a user — platform admin only. Creates a time-limited JWT."""
    # Verify target user exists
    user_result = await ctx.db.execute(select(User).where(User.id == req.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Verify membership
    membership_result = await ctx.db.execute(
        select(TenantMembership).where(
            TenantMembership.user_id == req.user_id,
            TenantMembership.tenant_id == req.tenant_id,
            TenantMembership.is_active == True,
        )
    )
    membership = membership_result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not a member of this tenant")

    permissions = membership.permissions or get_permissions_for_role(membership.role)

    # Issue short-lived JWT (1 hour max for impersonation)
    token = create_jwt_token(
        tenant_id=req.tenant_id,
        user_id=req.user_id,
        company_id=req.tenant_id,
        designation=membership.designation or "member",
        permissions=permissions,
        domain=user.domain,
    )

    logger.warning(
        "user_impersonated",
        admin_user_id=ctx.user.user_id,
        target_user_id=req.user_id,
        target_tenant_id=req.tenant_id,
    )

    return ImpersonateResponse(
        token=token,
        user_id=req.user_id,
        tenant_id=req.tenant_id,
        message=f"Impersonating {user.email} — token valid for standard duration",
    )


@router.get(
    "/usage",
    response_model=UsageStats,
    dependencies=[Depends(require_permission(Permission.PLATFORM_ADMIN))],
)
async def platform_usage_analytics(
    ctx: TenantContext = Depends(get_tenant_context),
) -> UsageStats:
    """Platform-wide usage analytics — platform admin only."""
    db = ctx.db

    total_tenants = (await db.execute(select(func.count(Tenant.id)))).scalar() or 0
    active_tenants = (await db.execute(
        select(func.count(Tenant.id)).where(Tenant.is_active == True)
    )).scalar() or 0

    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0

    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    active_users_30d = (await db.execute(
        select(func.count(User.id)).where(User.last_login >= thirty_days_ago)
    )).scalar() or 0

    total_articles = (await db.execute(select(func.count(Article.id)))).scalar() or 0
    total_predictions = (await db.execute(select(func.count(PredictionReport.id)))).scalar() or 0

    # Tenants by industry
    industry_result = await db.execute(
        select(Tenant.industry, func.count(Tenant.id))
        .where(Tenant.industry.isnot(None))
        .group_by(Tenant.industry)
    )
    tenants_by_industry = {row[0]: row[1] for row in industry_result.all()}

    return UsageStats(
        total_tenants=total_tenants,
        active_tenants=active_tenants,
        total_users=total_users,
        active_users_30d=active_users_30d,
        total_articles=total_articles,
        total_predictions=total_predictions,
        tenants_by_industry=tenants_by_industry,
    )


@router.get(
    "/usage/tenants",
    response_model=list[TenantUsageDetail],
    dependencies=[Depends(require_permission(Permission.PLATFORM_ADMIN))],
)
async def per_tenant_usage(
    limit: int = Query(default=50, le=200),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[TenantUsageDetail]:
    """Per-tenant usage breakdown — platform admin only."""
    result = await ctx.db.execute(
        select(Tenant).where(Tenant.is_active == True).limit(limit)
    )
    tenants = result.scalars().all()

    details = []
    for t in tenants:
        user_count = (await ctx.db.execute(
            select(func.count(TenantMembership.id)).where(TenantMembership.tenant_id == t.id)
        )).scalar() or 0

        article_count = (await ctx.db.execute(
            select(func.count(Article.id)).where(Article.tenant_id == t.id)
        )).scalar() or 0

        prediction_count = (await ctx.db.execute(
            select(func.count(PredictionReport.id)).where(PredictionReport.tenant_id == t.id)
        )).scalar() or 0

        details.append(TenantUsageDetail(
            tenant_id=t.id, tenant_name=t.name,
            user_count=user_count, article_count=article_count,
            prediction_count=prediction_count, last_activity=t.updated_at,
        ))

    return details


# --- Tenant Admin Endpoints (manage own tenant) ---

@router.get(
    "/users",
    response_model=list[UserSummary],
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
async def list_tenant_users(
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[UserSummary]:
    """List all users in the current tenant — tenant admin only."""
    result = await ctx.db.execute(
        select(TenantMembership, User)
        .join(User, TenantMembership.user_id == User.id)
        .where(TenantMembership.tenant_id == ctx.tenant_id)
    )
    rows = result.all()

    return [
        UserSummary(
            id=user.id, email=user.email, name=user.name,
            designation=membership.designation, domain=user.domain,
            role=membership.role,
            permissions=membership.permissions or [],
            is_active=membership.is_active,
            last_login=user.last_login,
        )
        for membership, user in rows
    ]


@router.patch(
    "/users/{user_id}/role",
    response_model=UserSummary,
    dependencies=[Depends(require_permission(Permission.MANAGE_ROLES))],
)
async def update_user_role(
    user_id: str,
    req: UpdateUserRoleRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> UserSummary:
    """Update a user's role and permissions within this tenant."""
    # Block self-role-change to prevent privilege escalation
    if user_id == ctx.user.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role. Ask another admin.",
        )

    result = await ctx.db.execute(
        select(TenantMembership).where(
            TenantMembership.tenant_id == ctx.tenant_id,
            TenantMembership.user_id == user_id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found in tenant")

    # Validate role
    valid_roles = [r.value for r in Role if r != Role.PLATFORM_ADMIN]
    if req.role not in valid_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Valid: {valid_roles}",
        )

    membership.role = req.role
    if req.custom_permissions is not None:
        membership.permissions = req.custom_permissions
    else:
        membership.permissions = get_permissions_for_role(req.role)

    await ctx.db.flush()

    user_result = await ctx.db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one()

    logger.info("user_role_updated", user_id=user_id, new_role=req.role, tenant_id=ctx.tenant_id)

    return UserSummary(
        id=user.id, email=user.email, name=user.name,
        designation=membership.designation, domain=user.domain,
        role=membership.role, permissions=membership.permissions or [],
        is_active=membership.is_active, last_login=user.last_login,
    )


@router.patch(
    "/users/{user_id}/deactivate",
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
async def deactivate_user(
    user_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Deactivate a user's membership in this tenant."""
    if user_id == ctx.user.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate yourself",
        )

    result = await ctx.db.execute(
        select(TenantMembership).where(
            TenantMembership.tenant_id == ctx.tenant_id,
            TenantMembership.user_id == user_id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found in tenant")

    membership.is_active = False
    await ctx.db.flush()

    logger.info("user_deactivated", user_id=user_id, tenant_id=ctx.tenant_id)
    return {"status": "deactivated", "user_id": user_id}


@router.get("/roles")
async def list_available_roles(
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[dict]:
    """List all available roles and their permissions."""
    roles = []
    for role in Role:
        if role == Role.PLATFORM_ADMIN:
            continue  # Don't expose platform admin role
        permissions = get_permissions_for_role(role.value)
        roles.append({
            "role": role.value,
            "label": role.name.replace("_", " ").title(),
            "permissions": permissions,
            "permission_count": len(permissions),
        })
    return roles
