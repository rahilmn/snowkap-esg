"""Campaigns router — newsletter, peer comparison, leadership content generation.

Fix 3: Implements /generate endpoint that wires Content + Competitive agents
to produce campaign content, peer reports, and leadership communications.
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from backend.core import llm
from backend.core.dependencies import TenantContext, get_tenant_context
from backend.models.company import Company
from backend.models.news import Article

logger = structlog.get_logger()
router = APIRouter()


class CampaignGenerateRequest(BaseModel):
    type: str  # "newsletter", "peer_comparison", "leadership_brief", "disclosure_draft"
    topic: str | None = None
    frameworks: list[str] | None = None


class CampaignResponse(BaseModel):
    type: str
    title: str
    content: str
    frameworks_referenced: list[str]
    articles_used: int


@router.get("/")
async def list_campaigns(
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """List campaigns for this tenant."""
    return {"campaigns": [], "total": 0}


@router.post("/generate", response_model=CampaignResponse)
async def generate_campaign(
    req: CampaignGenerateRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> CampaignResponse:
    """Generate campaign content using specialist AI agents.

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
        import re
        fw_refs = list(set(re.findall(r'(?:BRSR|GRI|TCFD|ESRS|CDP|IFRS|CSRD|SASB)[:\s]?\w*', content)))

        return CampaignResponse(
            type=req.type,
            title=f"{req.type.replace('_', ' ').title()} — {company_name}",
            content=content.strip(),
            frameworks_referenced=fw_refs[:10],
            articles_used=len(articles),
        )
    except Exception as e:
        logger.error("campaign_generation_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")
