"""Auth router — 3-way login (direct auth, no magic link).

Per CLAUDE.md Auth Model:
  Domain → Designation → Company Name → JWT
  No passwords. No OTP. Domain-gated. Auto-provisioning.

  POST /auth/resolve-domain  — takes domain, returns company info or creates prospect
  POST /auth/login           — validates email, provisions user/tenant, issues JWT
  POST /auth/returning-user  — email-only login for existing users, issues JWT
"""

import re
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.permissions import get_permissions_for_role, map_designation_to_role
from backend.core.security import (
    create_jwt_token,
    extract_domain_from_email,
    is_corporate_domain,
    validate_email_domain_match,
)
from backend.models.tenant import Tenant, TenantMembership
from backend.models.user import User

logger = structlog.get_logger()
router = APIRouter()


# --- Request / Response schemas ---

def _sanitize(text: str) -> str:
    """Strip HTML tags and script content."""
    return re.sub(r'<[^>]+>', '', text).strip()


class ResolveDomainRequest(BaseModel):
    domain: str

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("Domain cannot be empty")
        # Must look like a domain: alphanumeric + dots + hyphens, no HTML
        if not re.match(r'^[a-z0-9]([a-z0-9-]*\.)+[a-z]{2,}$', v):
            raise ValueError("Invalid domain format")
        return v

class ResolveDomainResponse(BaseModel):
    domain: str
    company_name: str | None = None
    industry: str | None = None
    is_existing: bool = False
    tenant_id: str | None = None

class LoginRequest(BaseModel):
    email: EmailStr
    domain: str
    designation: str
    company_name: str
    name: str = ""

class LoginResponse(BaseModel):
    token: str
    user_id: str
    tenant_id: str
    company_id: str | None = None
    designation: str
    permissions: list[str]
    domain: str
    name: str | None = None

class ReturningUserRequest(BaseModel):
    email: EmailStr


class MeResponse(BaseModel):
    """Stage 8.5: Current user info for frontend."""
    user_id: str
    email: str
    name: str | None = None
    domain: str
    designation: str | None = None
    tenant_id: str | None = None
    last_login: str | None = None  # ISO format for FOMO "new since" calc


# --- Endpoints ---

@router.post("/resolve-domain", response_model=ResolveDomainResponse)
async def resolve_domain(
    req: ResolveDomainRequest,
    db: AsyncSession = Depends(get_db),
) -> ResolveDomainResponse:
    """Step 1: Resolve company domain — returns company info or signals new prospect."""
    domain = req.domain.lower().strip()

    if not is_corporate_domain(domain):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Personal email domains are not allowed. Please use your corporate email domain.",
        )

    # Check if tenant already exists for this domain
    result = await db.execute(select(Tenant).where(Tenant.domain == domain))
    tenant = result.scalar_one_or_none()

    if tenant:
        logger.info("domain_resolved_existing", domain=domain, tenant_id=tenant.id)
        return ResolveDomainResponse(
            domain=domain,
            company_name=tenant.name,
            industry=tenant.industry,
            is_existing=True,
            tenant_id=tenant.id,
        )

    logger.info("domain_resolved_new", domain=domain)
    return ResolveDomainResponse(domain=domain, is_existing=False)


@router.post("/login", response_model=LoginResponse)
async def login(
    req: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Direct login: validate email, provision user/tenant, issue JWT.

    Per CLAUDE.md Rule #8: email domain must match company domain.
    """
    email = req.email.lower().strip()
    domain = req.domain.lower().strip()
    safe_name = _sanitize(req.name)[:200]
    safe_company_name = _sanitize(req.company_name)[:200]
    safe_designation = _sanitize(req.designation)[:100]

    # Validate email domain matches company domain
    if not validate_email_domain_match(email, domain):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Email domain must match company domain '{domain}'",
        )

    if not is_corporate_domain(domain):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Personal email domains are not allowed.",
        )

    # Find or create user
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            email=email,
            domain=domain,
            designation=safe_designation,
            name=safe_name,
        )
        try:
            db.add(user)
            await db.flush()
        except IntegrityError:
            await db.rollback()
            result = await db.execute(select(User).where(User.email == email))
            user = result.scalar_one()
    else:
        user.name = safe_name or user.name

    user.last_login = datetime.now(timezone.utc)

    # Find or create tenant (auto-provisioning per CLAUDE.md)
    result = await db.execute(select(Tenant).where(Tenant.domain == domain))
    tenant = result.scalar_one_or_none()

    is_new_tenant = False
    if not tenant:
        # Auto-classify industry via Claude (45 SASB categories)
        from backend.services.auth_service import classify_industry
        classification = await classify_industry(
            safe_company_name or domain,
            domain,
        )

        tenant = Tenant(
            name=safe_company_name or domain,
            domain=domain,
            industry=classification.get("industry"),
            sasb_category=classification.get("sasb_category"),
            sustainability_query=classification.get("sustainability_query"),
            general_query=classification.get("general_query"),
        )
        db.add(tenant)
        await db.flush()
        is_new_tenant = True
        logger.info(
            "tenant_auto_provisioned",
            tenant_id=tenant.id, domain=domain,
            industry=tenant.industry,
        )

    # Ensure membership exists
    result = await db.execute(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant.id,
            TenantMembership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()

    role = map_designation_to_role(safe_designation or "member")
    permissions = get_permissions_for_role(role)

    if not membership:
        membership = TenantMembership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=role,
            designation=safe_designation,
            permissions=permissions,
        )
        db.add(membership)

    # Look up actual company_id for this tenant
    from backend.models.company import Company
    company_result = await db.execute(
        select(Company).where(Company.tenant_id == tenant.id).limit(1)
    )
    company = company_result.scalar_one_or_none()
    actual_company_id = company.id if company else tenant.id

    # Issue JWT per CLAUDE.md spec
    jwt_token = create_jwt_token(
        tenant_id=tenant.id,
        user_id=user.id,
        company_id=actual_company_id,
        designation=safe_designation or "member",
        permissions=permissions,
        domain=domain,
    )

    logger.info("user_authenticated", user_id=user.id, tenant_id=tenant.id, company_id=actual_company_id, domain=domain)

    # Post-login triggers for new tenants
    if is_new_tenant:
        # Phase 3: Auto-provision company from domain (LLM discovers facilities, suppliers, industry)
        try:
            from backend.services.company_intelligence import auto_provision_company
            provision_result = await auto_provision_company(domain, tenant.id, db)
            logger.info("company_auto_provisioned", result=provision_result)
            # Refresh tenant after provisioning may have updated it
            await db.refresh(tenant)
        except Exception as e:
            logger.warning("company_auto_provision_failed", error=str(e))
            # Fallback: still trigger basic provisioning below

        from backend.tasks.news_tasks import ingest_news_for_tenant
        ingest_news_for_tenant.delay(
            tenant.id,
            tenant.name,
            tenant.sustainability_query or "",
            tenant.general_query or "",
        )
        logger.info("news_curation_triggered", tenant_id=tenant.id)

        from backend.tasks.ontology_tasks import provision_tenant_ontology_task
        provision_tenant_ontology_task.delay(
            tenant.id,
            tenant.name,
            tenant.industry,
            tenant.sasb_category,
            tenant.domain,
        )

    # Ensure all DB changes are committed before returning
    await db.commit()

    return LoginResponse(
        token=jwt_token,
        user_id=user.id,
        tenant_id=tenant.id,
        designation=safe_designation or "member",
        permissions=permissions,
        domain=domain,
        name=user.name,
    )


@router.post("/returning-user", response_model=LoginResponse)
async def returning_user_login(
    req: ReturningUserRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Returning users: email-only → JWT (skip domain/designation)."""
    email = req.email.lower().strip()

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No account found for this email. Please sign up first.",
        )

    user.last_login = datetime.now(timezone.utc)

    # Get their membership for domain/designation/permissions
    result = await db.execute(
        select(TenantMembership).where(TenantMembership.user_id == user.id, TenantMembership.is_active == True)
    )
    membership = result.scalar_one_or_none()

    if not membership:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active membership found. Please sign up again.",
        )

    # Get tenant
    result = await db.execute(select(Tenant).where(Tenant.id == membership.tenant_id))
    tenant = result.scalar_one_or_none()

    permissions = membership.permissions or get_permissions_for_role(membership.role or "member")

    # Look up actual company_id
    from backend.models.company import Company
    company_result = await db.execute(
        select(Company).where(Company.tenant_id == tenant.id).limit(1)
    )
    company = company_result.scalar_one_or_none()
    actual_company_id = company.id if company else tenant.id

    jwt_token = create_jwt_token(
        tenant_id=tenant.id,
        user_id=user.id,
        company_id=actual_company_id,
        designation=membership.designation or "member",
        permissions=permissions,
        domain=user.domain,
    )

    logger.info("returning_user_authenticated", email=email, user_id=user.id)

    return LoginResponse(
        token=jwt_token,
        user_id=user.id,
        tenant_id=tenant.id,
        designation=membership.designation or "member",
        permissions=permissions,
        domain=user.domain,
        name=user.name,
    )


# --- Stage 8.5: /me endpoint for frontend ---

from backend.core.dependencies import TenantContext as _TC, get_tenant_context as _get_ctx


@router.get("/me", response_model=MeResponse)
async def get_current_user_info(
    ctx: _TC = Depends(_get_ctx),
) -> MeResponse:
    """Stage 8.5: Return current authenticated user's profile with last_login.

    Used by frontend for:
    - IntroCard FOMO "new since last visit" calculation
    - User avatar and name display
    - Session management

    Requires valid JWT in Authorization header.
    """
    user = ctx.user

    # Get tenant membership for designation
    membership_result = await ctx.db.execute(
        select(TenantMembership).where(
            TenantMembership.tenant_id == ctx.tenant_id,
            TenantMembership.user_id == user.user_id,
        )
    )
    membership = membership_result.scalar_one_or_none()

    # Get user record for last_login
    user_result = await ctx.db.execute(
        select(User).where(User.id == user.user_id)
    )
    user_record = user_result.scalar_one_or_none()

    last_login = None
    if user_record and hasattr(user_record, "last_login") and user_record.last_login:
        last_login = user_record.last_login.isoformat()

    return MeResponse(
        user_id=user.user_id,
        email=getattr(user_record, "email", "") if user_record else "",
        name=getattr(user_record, "name", None) if user_record else None,
        domain=getattr(user, "domain", ""),
        designation=membership.designation if membership else None,
        tenant_id=ctx.tenant_id,
        last_login=last_login,
    )
