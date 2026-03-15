"""News router — news feed + curation endpoints."""

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.core.dependencies import TenantContext, get_tenant_context
from backend.models.company import Company
from backend.models.news import Article, ArticleScore, CausalChain
from backend.models.prediction import PredictionReport

logger = structlog.get_logger()
router = APIRouter()


class ArticleScoreResponse(BaseModel):
    company_id: str
    company_name: str
    impact_score: float
    causal_hops: int
    relationship_type: str
    explanation: str | None = None
    financial_exposure: float | None = None
    frameworks: list[str] = []


class ArticlePredictionResponse(BaseModel):
    id: str
    title: str
    summary: str | None = None
    prediction_text: str | None = None
    confidence_score: float
    financial_impact: float | None = None
    time_horizon: str | None = None
    risk_level: str | None = None
    status: str


class ArticleResponse(BaseModel):
    id: str
    title: str
    summary: str | None = None
    source: str | None = None
    url: str | None = None
    published_at: str | None = None
    esg_pillar: str | None = None
    sentiment: str | None = None
    entities: list[str] = []
    impact_scores: list[ArticleScoreResponse] = []
    predictions: list[ArticlePredictionResponse] = []
    frameworks: list[str] = []


class CausalChainResponse(BaseModel):
    id: str
    article_id: str
    company_id: str
    chain_path: list | None
    hops: int
    relationship_type: str
    impact_score: float
    explanation: str | None


class NewsFeedResponse(BaseModel):
    articles: list[ArticleResponse]
    total: int


async def _load_articles_with_scores(
    ctx: TenantContext, limit: int = 50, offset: int = 0,
) -> list[ArticleResponse]:
    """Load articles and join impact scores + company names."""
    query = select(Article).where(Article.tenant_id == ctx.tenant_id)
    query = query.order_by(Article.created_at.desc()).limit(limit).offset(offset)

    result = await ctx.db.execute(query)
    articles = result.scalars().all()

    if not articles:
        return []

    # Load all scores for these articles in one query
    article_ids = [a.id for a in articles]
    scores_result = await ctx.db.execute(
        select(ArticleScore).where(
            ArticleScore.article_id.in_(article_ids),
            ArticleScore.tenant_id == ctx.tenant_id,
        )
    )
    all_scores = scores_result.scalars().all()

    # Load company names
    company_ids = list({s.company_id for s in all_scores})
    company_names: dict[str, str] = {}
    if company_ids:
        companies_result = await ctx.db.execute(
            select(Company.id, Company.name).where(Company.id.in_(company_ids))
        )
        company_names = {row[0]: row[1] for row in companies_result.all()}

    # Load causal chains for relationship_type + explanation
    chains_result = await ctx.db.execute(
        select(CausalChain).where(
            CausalChain.article_id.in_(article_ids),
            CausalChain.tenant_id == ctx.tenant_id,
        )
    )
    all_chains = chains_result.scalars().all()
    # Index by (article_id, company_id)
    chain_lookup: dict[tuple[str, str], CausalChain] = {}
    for c in all_chains:
        chain_lookup[(c.article_id, c.company_id)] = c

    # Load predictions linked to these articles
    preds_result = await ctx.db.execute(
        select(PredictionReport).where(
            PredictionReport.article_id.in_(article_ids),
            PredictionReport.tenant_id == ctx.tenant_id,
        )
    )
    all_preds = preds_result.scalars().all()
    preds_by_article: dict[str, list[ArticlePredictionResponse]] = {}
    for p in all_preds:
        consensus = p.agent_consensus or {}
        pred_resp = ArticlePredictionResponse(
            id=p.id,
            title=p.title,
            summary=p.summary,
            prediction_text=p.prediction_text,
            confidence_score=p.confidence_score,
            financial_impact=p.financial_impact,
            time_horizon=p.time_horizon,
            risk_level=consensus.get("risk_level"),
            status=p.status,
        )
        preds_by_article.setdefault(p.article_id, []).append(pred_resp)

    # Collect frameworks per article from chains + scores
    frameworks_by_article: dict[str, set[str]] = {}
    for c in all_chains:
        if c.framework_alignment:
            frameworks_by_article.setdefault(c.article_id, set()).update(c.framework_alignment)
    for s in all_scores:
        if s.frameworks:
            frameworks_by_article.setdefault(s.article_id, set()).update(s.frameworks)

    # Group scores by article_id
    scores_by_article: dict[str, list[ArticleScoreResponse]] = {}
    for s in all_scores:
        chain = chain_lookup.get((s.article_id, s.company_id))
        chain_frameworks = list(chain.framework_alignment) if chain and chain.framework_alignment else []
        score_frameworks = list(s.frameworks) if s.frameworks else []
        merged = list(set(chain_frameworks + score_frameworks))
        score_resp = ArticleScoreResponse(
            company_id=s.company_id,
            company_name=company_names.get(s.company_id, "Unknown"),
            impact_score=s.impact_score,
            causal_hops=s.causal_hops,
            relationship_type=chain.relationship_type if chain else "directOperational",
            explanation=chain.explanation if chain else None,
            financial_exposure=s.financial_exposure,
            frameworks=merged,
        )
        scores_by_article.setdefault(s.article_id, []).append(score_resp)

    return [
        ArticleResponse(
            id=a.id,
            title=a.title,
            summary=a.summary,
            source=a.source,
            url=a.url,
            published_at=a.published_at,
            esg_pillar=a.esg_pillar,
            sentiment=a.sentiment,
            entities=a.entities if isinstance(a.entities, list) else [],
            impact_scores=sorted(
                scores_by_article.get(a.id, []),
                key=lambda x: x.impact_score,
                reverse=True,
            ),
            predictions=preds_by_article.get(a.id, []),
            frameworks=sorted(frameworks_by_article.get(a.id, set())),
        )
        for a in articles
    ]


@router.get("/feed", response_model=NewsFeedResponse)
async def get_news_feed(
    company_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: TenantContext = Depends(get_tenant_context),
) -> NewsFeedResponse:
    """Get tenant-scoped news feed with impact scores."""
    articles = await _load_articles_with_scores(ctx, limit, offset)
    return NewsFeedResponse(articles=articles, total=len(articles))


@router.get("/causal-chains/{article_id}", response_model=list[CausalChainResponse])
async def get_causal_chains(
    article_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[CausalChainResponse]:
    """Get all causal chains for a specific article."""
    result = await ctx.db.execute(
        select(CausalChain).where(
            CausalChain.tenant_id == ctx.tenant_id,
            CausalChain.article_id == article_id,
        ).order_by(CausalChain.impact_score.desc())
    )
    chains = result.scalars().all()
    return [
        CausalChainResponse(
            id=c.id, article_id=c.article_id, company_id=c.company_id,
            chain_path=c.chain_path, hops=c.hops, relationship_type=c.relationship_type,
            impact_score=c.impact_score, explanation=c.explanation,
        )
        for c in chains
    ]
