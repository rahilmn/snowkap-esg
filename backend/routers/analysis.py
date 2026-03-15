"""Analysis router — ESG analysis endpoints."""

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from backend.core.dependencies import TenantContext, get_tenant_context
from backend.models.analysis import Analysis

logger = structlog.get_logger()
router = APIRouter()


class AnalysisResponse(BaseModel):
    id: str
    company_id: str
    analysis_type: str
    title: str
    status: str
    frameworks: list[str] | None


class AnalysisList(BaseModel):
    analyses: list[AnalysisResponse]
    total: int


@router.get("/", response_model=AnalysisList)
async def list_analyses(
    company_id: str | None = None,
    ctx: TenantContext = Depends(get_tenant_context),
) -> AnalysisList:
    """List analyses — tenant-scoped, optionally filtered by company."""
    query = select(Analysis).where(Analysis.tenant_id == ctx.tenant_id)
    if company_id:
        query = query.where(Analysis.company_id == company_id)
    query = query.order_by(Analysis.created_at.desc())

    result = await ctx.db.execute(query)
    analyses = result.scalars().all()
    return AnalysisList(
        analyses=[
            AnalysisResponse(
                id=a.id, company_id=a.company_id, analysis_type=a.analysis_type,
                title=a.title, status=a.status, frameworks=a.frameworks,
            )
            for a in analyses
        ],
        total=len(analyses),
    )
