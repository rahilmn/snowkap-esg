"""Companies router — ESG analysis target CRUD.

Per CLAUDE.md: companies = ESG analysis targets (NOT tenants).
Every query filters by tenant_id via TenantContext.
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.dependencies import TenantContext, get_tenant_context
from backend.models.company import Company

logger = structlog.get_logger()
router = APIRouter()


class CompanyCreate(BaseModel):
    name: str
    slug: str
    domain: str | None = None
    industry: str | None = None
    sasb_category: str | None = None


class CompanyResponse(BaseModel):
    id: str
    name: str
    slug: str
    domain: str | None
    industry: str | None
    sasb_category: str | None
    status: str


class CompanyList(BaseModel):
    companies: list[CompanyResponse]
    total: int


@router.get("", response_model=CompanyList)
@router.get("/", response_model=CompanyList, include_in_schema=False)
async def list_companies(
    ctx: TenantContext = Depends(get_tenant_context),
) -> CompanyList:
    """List all companies for this tenant."""
    result = await ctx.db.execute(
        select(Company).where(Company.tenant_id == ctx.tenant_id).order_by(Company.name)
    )
    companies = result.scalars().all()
    return CompanyList(
        companies=[
            CompanyResponse(
                id=c.id, name=c.name, slug=c.slug, domain=c.domain,
                industry=c.industry, sasb_category=c.sasb_category, status=c.status,
            )
            for c in companies
        ],
        total=len(companies),
    )


@router.post("", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_company(
    req: CompanyCreate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> CompanyResponse:
    """Create a new ESG analysis target company within this tenant."""
    company = Company(
        tenant_id=ctx.tenant_id,
        name=req.name,
        slug=req.slug,
        domain=req.domain,
        industry=req.industry,
        sasb_category=req.sasb_category,
    )
    ctx.db.add(company)
    await ctx.db.flush()

    logger.info("company_created", company_id=company.id, name=company.name)
    return CompanyResponse(
        id=company.id, name=company.name, slug=company.slug, domain=company.domain,
        industry=company.industry, sasb_category=company.sasb_category, status=company.status,
    )


@router.get("/{company_id}", response_model=CompanyResponse)
async def get_company(
    company_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
) -> CompanyResponse:
    """Get a specific company — tenant-scoped."""
    result = await ctx.db.execute(
        select(Company).where(Company.id == company_id, Company.tenant_id == ctx.tenant_id)
    )
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    return CompanyResponse(
        id=company.id, name=company.name, slug=company.slug, domain=company.domain,
        industry=company.industry, sasb_category=company.sasb_category, status=company.status,
    )
