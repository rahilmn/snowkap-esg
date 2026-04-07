"""Campaigns router — newsletter, peer comparison, leadership content generation.

Full CRUD: list, get, generate (create), delete campaigns.
Content is persisted to the campaigns table for history and editing.
"""

import re

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from backend.core import llm
from backend.core.dependencies import TenantContext, get_tenant_context
from backend.models.campaign import ContentCampaign as Campaign
from backend.models.company import Company
from backend.models.news import Article

logger = structlog.get_logger()
router = APIRouter()


# --- Request / Response schemas ---

class CampaignGenerateRequest(BaseModel):
    type: str  # "newsletter", "peer_comparison", "leadership_brief", "disclosure_draft"
    topic: str | None = None
    frameworks: list[str] | None = None


class CampaignResponse(BaseModel):
    id: str
    type: str
    title: str
    content: str
    topic: str | None
    status: str
    frameworks_referenced: list[str]
    articles_used: int
    created_at: str | None = None


class CampaignListResponse(BaseModel):
    campaigns: list[CampaignResponse]
    total: int


# --- Endpoints ---

@router.get("/", response_model=CampaignListResponse)
async def list_campaigns(
    type: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(get_tenant_context),
) -> CampaignListResponse:
    """List campaigns for this tenant."""
    query = select(Campaign).where(Campaign.tenant_id == ctx.tenant_id)
    if type:
        query = query.where(Campaign.type == type)
    query = query.order_by(Campaign.created_at.desc()).limit(limit).offset(offset)

    result = await ctx.db.execute(query)
    campaigns = result.scalars().all()

    count_q = select(func.count(Campaign.id)).where(Campaign.tenant_id == ctx.tenant_id)
    if type:
        count_q = count_q.where(Campaign.type == type)
    total = (await ctx.db.execute(count_q)).scalar() or 0

    return CampaignListResponse(
        campaigns=[
            CampaignResponse(
                id=c.id, type=c.type, title=c.title, content=c.content,
                topic=c.topic, status=c.status,
                frameworks_referenced=c.frameworks_referenced or [],
                articles_used=c.articles_used or 0,
                created_at=c.created_at.isoformat() if c.created_at else None,
            )
            for c in campaigns
        ],
        total=total,
    )


@router.get("/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(
    campaign_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
) -> CampaignResponse:
    """Get a single campaign by ID."""
    result = await ctx.db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.tenant_id == ctx.tenant_id,
        )
    )
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return CampaignResponse(
        id=c.id, type=c.type, title=c.title, content=c.content,
        topic=c.topic, status=c.status,
        frameworks_referenced=c.frameworks_referenced or [],
        articles_used=c.articles_used or 0,
        created_at=c.created_at.isoformat() if c.created_at else None,
    )


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(
    campaign_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
) -> None:
    """Delete a campaign."""
    result = await ctx.db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.tenant_id == ctx.tenant_id,
        )
    )
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    await ctx.db.delete(c)
    await ctx.db.flush()


@router.post("/generate", response_model=CampaignResponse)
async def generate_campaign(
    req: CampaignGenerateRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> CampaignResponse:
    """Generate campaign content using specialist AI agents and persist it.

    Types:
    - newsletter: ESG newsletter draft from recent high-priority articles
    - peer_comparison: Competitive ESG landscape analysis
    - leadership_brief: Board/CXO communication on ESG positioning
    - disclosure_draft: Framework-specific disclosure draft (BRSR, GRI, etc.)
    """
    if not llm.is_configured():
        raise HTTPException(status_code=503, detail="AI service not configured")

    # Get company + recent articles for context
    comp_result = await ctx.db.execute(
        select(Company).where(Company.tenant_id == ctx.tenant_id).limit(1)
    )
    company = comp_result.scalars().first()
    company_name = company.name if company else "Your Company"
    competitors = company.competitors if company else []

    art_result = await ctx.db.execute(
        select(Article).where(
            Article.tenant_id == ctx.tenant_id,
            Article.priority_score.isnot(None),
        ).order_by(Article.priority_score.desc()).limit(5)
    )
    articles = art_result.scalars().all()
    article_summaries = "\n".join(
        f"- [{a.priority_level}] {a.title} (sentiment: {a.sentiment_score}, type: {a.content_type})"
        for a in articles
    )

    # Load specialist personality
    from pathlib import Path
    personalities_dir = Path(__file__).parent.parent / "agent" / "personalities"

    if req.type == "peer_comparison":
        personality = (personalities_dir / "competitive.md").read_text(encoding="utf-8")
        comp_list = ", ".join(c.get("name", "?") for c in (competitors or [])[:5]) if competitors else "industry peers"
        user_prompt = f"""Generate a comprehensive peer ESG comparison report for {company_name}.

Competitors: {comp_list}
Topic: {req.topic or 'overall ESG positioning'}

Recent news context:
{article_summaries}

Format: Executive summary (3 sentences), then for each competitor: their ESG position, how {company_name} compares, and recommended actions. End with strategic recommendations."""

    elif req.type == "leadership_brief":
        personality = (personalities_dir / "executive.md").read_text(encoding="utf-8")
        user_prompt = f"""Generate a board-level ESG leadership brief for {company_name}.

Topic: {req.topic or 'quarterly ESG update'}
Frameworks: {', '.join(req.frameworks or ['BRSR', 'GRI'])}

Recent high-priority developments:
{article_summaries}

Format: Use SCQA (Situation, Complication, Question, Answer). Include specific framework references and quantified recommendations. Maximum 500 words."""

    elif req.type == "disclosure_draft":
        personality = (personalities_dir / "content.md").read_text(encoding="utf-8")
        fw = req.frameworks[0] if req.frameworks else "BRSR"
        user_prompt = f"""Draft an ESG disclosure section for {company_name} under the {fw} framework.

Topic: {req.topic or 'general sustainability disclosure'}

Recent developments for context:
{article_summaries}

Format: Framework-compliant disclosure language. Reference specific indicator codes. Include data placeholders where actual figures are needed. Maximum 800 words."""

    else:  # newsletter
        personality = (personalities_dir / "content.md").read_text(encoding="utf-8")
        user_prompt = f"""Generate an ESG newsletter for {company_name} stakeholders.

Topic: {req.topic or 'monthly ESG update'}

Recent developments:
{article_summaries}

Format: Engaging headline, introduction (2 sentences), 3-4 key stories with brief analysis, closing with forward outlook. Professional but accessible tone. Maximum 600 words."""

    try:
        content = await llm.chat(
            system=personality,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=1500,
        )

        # Extract frameworks referenced
        fw_refs = list(set(re.findall(r'(?:BRSR|GRI|TCFD|ESRS|CDP|IFRS|CSRD|SASB)[:\s]?\w*', content)))

        title = f"{req.type.replace('_', ' ').title()} — {company_name}"

        # Persist to database
        campaign = Campaign(
            tenant_id=ctx.tenant_id,
            type=req.type,
            title=title,
            content=content.strip(),
            topic=req.topic,
            status="draft",
            frameworks_referenced=fw_refs[:10],
            articles_used=len(articles),
            created_by=ctx.user.id if ctx.user else None,
        )
        ctx.db.add(campaign)
        await ctx.db.flush()

        logger.info("campaign_generated", id=campaign.id, type=req.type, tenant_id=ctx.tenant_id)

        return CampaignResponse(
            id=campaign.id,
            type=campaign.type,
            title=campaign.title,
            content=campaign.content,
            topic=campaign.topic,
            status=campaign.status,
            frameworks_referenced=fw_refs[:10],
            articles_used=len(articles),
            created_at=campaign.created_at.isoformat() if campaign.created_at else None,
        )
    except Exception as e:
        logger.error("campaign_generation_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")
