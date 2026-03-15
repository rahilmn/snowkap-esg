"""Auth router — 3-way login + magic link.

Per CLAUDE.md Auth Model:
  Domain → Designation → Company Name → Magic Link → JWT
  No passwords. No OTP. Domain-gated. Auto-provisioning.

Per MASTER_BUILD_PLAN Phase 2C:
  POST /auth/resolve-domain  — takes domain, returns company info or creates prospect
  POST /auth/magic-link      — sends login link (email domain must match company domain)
  GET  /auth/verify/{token}  — validates magic link, issues JWT
"""

from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.permissions import get_permissions_for_role, map_designation_to_role
from backend.core.security import (
    create_jwt_token,
    extract_domain_from_email,
    generate_magic_link_token,
    is_corporate_domain,
    validate_email_domain_match,
)
from backend.models.tenant import Tenant, TenantMembership
from backend.models.user import MagicLink, User

logger = structlog.get_logger()
router = APIRouter()


# --- Request / Response schemas ---

class ResolveDomainRequest(BaseModel):
    domain: str

class ResolveDomainResponse(BaseModel):
    domain: str
    company_name: str | None = None
    industry: str | None = None
    is_existing: bool = False
    tenant_id: str | None = None

class MagicLinkRequest(BaseModel):
    email: EmailStr
    domain: str
    designation: str
    company_name: str
    name: str = ""

class MagicLinkResponse(BaseModel):
    message: str
    email: str

class VerifyResponse(BaseModel):
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


@router.post("/magic-link", response_model=MagicLinkResponse)
async def send_magic_link(
    req: MagicLinkRequest,
    db: AsyncSession = Depends(get_db),
) -> MagicLinkResponse:
    """Step 2+3: Send magic link to work email.

    Per CLAUDE.md Rule #8: email domain must match company domain.
    """
    email = req.email.lower().strip()
    domain = req.domain.lower().strip()

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

    # Check if user already exists
    result = await db.execute(select(User).where(User.email == email))
    existing_user = result.scalar_one_or_none()

    # Create magic link token
    token = generate_magic_link_token()
    magic_link = MagicLink(
        email=email,
        token=token,
        domain=domain,
        designation=req.designation,
        company_name=req.company_name,
        name=req.name,
        user_id=existing_user.id if existing_user else None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.MAGIC_LINK_EXPIRE_MINUTES),
    )
    db.add(magic_link)

    # Send email via Celery task
    from backend.tasks.email_tasks import send_magic_link_task
    send_magic_link_task.delay(email, token, domain)
    logger.info("magic_link_created", email=email, domain=domain)

    return MagicLinkResponse(
        message="Magic link sent to your email",
        email=email,
    )


@router.get("/verify/{token}", response_model=VerifyResponse)
async def verify_magic_link(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> VerifyResponse:
    """Verify magic link token, provision user/tenant if needed, issue JWT."""
    result = await db.execute(
        select(MagicLink).where(MagicLink.token == token, MagicLink.used == False)
    )
    magic_link = result.scalar_one_or_none()

    if not magic_link:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired link")

    if magic_link.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Magic link has expired")

    # Mark as used
    magic_link.used = True
    magic_link.used_at = datetime.now(timezone.utc)

    # Find or create user
    result = await db.execute(select(User).where(User.email == magic_link.email))
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            email=magic_link.email,
            domain=magic_link.domain,
            designation=magic_link.designation,
            name=magic_link.name,
        )
        db.add(user)
        await db.flush()
    else:
        user.name = magic_link.name or user.name

    user.last_login = datetime.now(timezone.utc)

    # Find or create tenant (auto-provisioning per CLAUDE.md)
    result = await db.execute(select(Tenant).where(Tenant.domain == magic_link.domain))
    tenant = result.scalar_one_or_none()

    is_new_tenant = False
    if not tenant:
        # Auto-classify industry via Claude (45 SASB categories)
        from backend.services.auth_service import classify_industry
        classification = await classify_industry(
            magic_link.company_name or magic_link.domain,
            magic_link.domain,
        )

        tenant = Tenant(
            name=magic_link.company_name or magic_link.domain,
            domain=magic_link.domain,
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
            tenant_id=tenant.id, domain=magic_link.domain,
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

    role = map_designation_to_role(magic_link.designation or "member")
    permissions = get_permissions_for_role(role)

    if not membership:
        membership = TenantMembership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=role,
            designation=magic_link.designation,
            permissions=permissions,
        )
        db.add(membership)

    # Issue JWT per CLAUDE.md spec
    jwt_token = create_jwt_token(
        tenant_id=tenant.id,
        user_id=user.id,
        company_id=tenant.id,  # For now, company_id = tenant_id until companies are linked
        designation=magic_link.designation or "member",
        permissions=permissions,
        domain=magic_link.domain,
    )

    logger.info("user_authenticated", user_id=user.id, tenant_id=tenant.id, domain=magic_link.domain)

    # Post-login triggers for new tenants
    if is_new_tenant:
        # Trigger domain-driven news curation via Celery
        from backend.tasks.news_tasks import ingest_news_for_tenant
        ingest_news_for_tenant.delay(
            tenant.id,
            tenant.name,
            tenant.sustainability_query or "",
            tenant.general_query or "",
        )
        logger.info("news_curation_triggered", tenant_id=tenant.id)

        # Auto-provision company node in Jena knowledge graph (Phase 3)
        from backend.tasks.ontology_tasks import provision_tenant_ontology_task
        provision_tenant_ontology_task.delay(
            tenant.id,
            tenant.name,
            tenant.industry,
            tenant.sasb_category,
            tenant.domain,
        )

    return VerifyResponse(
        token=jwt_token,
        user_id=user.id,
        tenant_id=tenant.id,
        designation=magic_link.designation or "member",
        permissions=permissions,
        domain=magic_link.domain,
        name=user.name,
    )


@router.post("/returning-user", response_model=MagicLinkResponse)
async def returning_user_login(
    req: ReturningUserRequest,
    db: AsyncSession = Depends(get_db),
) -> MagicLinkResponse:
    """Returning users: email-only → magic link → JWT (skip domain/designation)."""
    email = req.email.lower().strip()

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found with this email. Please use the full registration flow.",
        )

    # Get their membership for domain/designation
    result = await db.execute(
        select(TenantMembership).where(TenantMembership.user_id == user.id, TenantMembership.is_active == True)
    )
    membership = result.scalar_one_or_none()

    token = generate_magic_link_token()
    magic_link = MagicLink(
        email=email,
        token=token,
        domain=user.domain,
        designation=membership.designation if membership else None,
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.MAGIC_LINK_EXPIRE_MINUTES),
    )
    db.add(magic_link)

    logger.info("returning_user_magic_link", email=email, user_id=user.id)

    return MagicLinkResponse(message="Magic link sent to your email", email=email)
