"""News router — news feed + curation endpoints.

Stage 8.5: Added /stats and /bookmark endpoints for frontend IntroCard + SavedNewsPage.
"""

from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from backend.core.dependencies import TenantContext, get_tenant_context
from backend.models.company import Company
from backend.models.news import Article, ArticleScore, CausalChain
from backend.models.prediction import PredictionReport

logger = structlog.get_logger()
router = APIRouter()


class FrameworkHit(BaseModel):
    """Stage 3.5: Enriched framework match with indicator-level detail."""
    framework: str             # "BRSR"
    indicator: str | None = None  # "P6"
    indicator_name: str | None = None  # "Environmental Protection"
    relevance: float | None = None  # 0.0-1.0
    explanation: str | None = None  # "Coal dependency triggers P6 disclosure"


class ArticleScoreResponse(BaseModel):
    company_id: str
    company_name: str
    impact_score: float
    causal_hops: int
    relationship_type: str
    content_label: str = "direct_impact"  # GAP-7: "direct_impact" | "competitive_intelligence" | "sector_news"
    explanation: str | None = None
    financial_exposure: float | None = None
    frameworks: list[str] = []
    framework_hits: list[FrameworkHit] = []  # Stage 3.5
    # Causal chain data for visualization
    chain_path: list | None = None        # [{nodes: [...], edges: [...]}]
    confidence: float | None = None
    framework_alignment: list[str] = []


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
    image_url: str | None = None
    published_at: str | None = None
    esg_pillar: str | None = None
    sentiment: str | None = None
    entities: list[str] = []
    impact_scores: list[ArticleScoreResponse] = []
    predictions: list[ArticlePredictionResponse] = []
    frameworks: list[str] = []
    framework_hits: list[FrameworkHit] = []  # Stage 3.5

    # Phase 1C: Enhanced sentiment + criticality (exposed to frontend)
    sentiment_score: float | None = None
    sentiment_confidence: float | None = None
    aspect_sentiments: dict | None = None
    content_type: str | None = None
    urgency: str | None = None
    time_horizon: str | None = None
    reversibility: str | None = None
    priority_score: float | None = None
    priority_level: str | None = None
    financial_signal: dict | None = None
    executive_insight: str | None = None

    # Advanced Intelligence (Phase 1-3)
    relevance_score: float | None = None
    relevance_breakdown: dict | None = None
    deep_insight: dict | None = None
    rereact_recommendations: dict | None = None

    # v2.0 Intelligence Modules
    nlp_extraction: dict | None = None
    esg_themes: dict | None = None
    framework_matches: list | None = None
    risk_matrix: dict | None = None
    geographic_signal: dict | None = None

    # Internal fields for scoring (not serialized to JSON, used in-memory)
    _content_type: str | None = None
    _esg_pillar: str | None = None
    _topics: list[str] | None = None
    _priority_score: float | None = None
    _published_at: str | None = None
    _sentiment_score: float | None = None

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, **data):
        # Extract internal fields before Pydantic validation
        internal = {}
        for key in ("_content_type", "_esg_pillar", "_topics", "_priority_score", "_published_at", "_sentiment_score"):
            if key in data:
                internal[key] = data.pop(key)
        super().__init__(**data)
        for key, val in internal.items():
            object.__setattr__(self, key, val)


# Stage 3.5: Framework indicator names for enriched responses
FRAMEWORK_INDICATOR_NAMES: dict[str, str] = {
    "BRSR:P1": "Ethics & Transparency",
    "BRSR:P2": "Product Safety",
    "BRSR:P3": "Employee Wellbeing",
    "BRSR:P4": "Stakeholder Engagement",
    "BRSR:P5": "Human Rights",
    "BRSR:P6": "Environmental Protection",
    "BRSR:P7": "Public Policy",
    "BRSR:P8": "Inclusive Growth",
    "BRSR:P9": "Customer Responsibility",
    "GRI:205": "Anti-corruption",
    "GRI:301": "Materials",
    "GRI:302": "Energy",
    "GRI:303": "Water and Effluents",
    "GRI:304": "Biodiversity",
    "GRI:305": "Emissions",
    "GRI:306": "Waste",
    "GRI:401": "Employment",
    "GRI:403": "Occupational Health and Safety",
    "GRI:405": "Diversity and Equal Opportunity",
    "GRI:413": "Local Communities",
    "GRI:414": "Supplier Social Assessment",
    "GRI:416": "Customer Health and Safety",
    "GRI:418": "Customer Privacy",
    "TCFD:Strategy": "Strategy",
    "TCFD:Risk": "Risk Management",
    "TCFD:Metrics": "Metrics and Targets",
    "TCFD:Governance": "Governance",
    "ESRS:E1": "Climate Change",
    "ESRS:E3": "Water and Marine Resources",
    "ESRS:E4": "Biodiversity and Ecosystems",
    "ESRS:E5": "Resource Use and Circular Economy",
    "ESRS:S1": "Own Workforce",
    "ESRS:S2": "Workers in Value Chain",
    "ESRS:S3": "Affected Communities",
    "ESRS:S4": "Consumers and End-users",
    "ESRS:G1": "Business Conduct",
    "CDP:Climate": "Climate Change",
    "CDP:Water": "Water Security",
    "CDP:Forests": "Forests",
}


def _parse_framework_tag(tag: str) -> FrameworkHit:
    """Parse a framework string like 'BRSR:P6' or 'GRI 305' into a FrameworkHit."""
    # Handle colon format: "BRSR:P6"
    if ":" in tag:
        parts = tag.split(":", 1)
        framework = parts[0].strip()
        indicator = parts[1].strip() if len(parts) > 1 else None
    # Handle space format: "GRI 305"
    elif " " in tag:
        parts = tag.split(" ", 1)
        framework = parts[0].strip()
        indicator = parts[1].strip() if len(parts) > 1 else None
    else:
        framework = tag.strip()
        indicator = None

    # Build lookup key
    lookup_key = f"{framework}:{indicator}" if indicator else framework
    indicator_name = FRAMEWORK_INDICATOR_NAMES.get(lookup_key)

    # Relevance scoring: Direct text mention → 0.9, Issue mapping → 0.7, Obligation → 0.5
    relevance = 0.7 if indicator else 0.5

    return FrameworkHit(
        framework=framework,
        indicator=indicator,
        indicator_name=indicator_name,
        relevance=relevance,
    )


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
    ctx: TenantContext,
    limit: int = 50,  # capped at 200 in endpoint
    offset: int = 0,
    sort_by: str = "priority",
    pillar_filter: str | None = None,
    content_type_filter: str | None = None,
) -> list[ArticleResponse]:
    """Load articles and join impact scores + company names.

    Phase 2C: Supports priority-based, recency, and impact sorting.
    """
    query = select(Article).where(Article.tenant_id == ctx.tenant_id)

    # Phase 2C: Apply filters
    if pillar_filter:
        query = query.where(Article.esg_pillar == pillar_filter)
    if content_type_filter:
        query = query.where(Article.content_type == content_type_filter)

    # Sort order
    if sort_by == "priority":
        # Home dashboard: priority first, filter out rejected articles
        # Use or_(is None) to include unanalyzed articles (NULL priority_level)
        from sqlalchemy import or_
        query = query.where(
            or_(Article.priority_level != "REJECTED", Article.priority_level.is_(None))
        )
        query = query.order_by(
            Article.priority_score.desc().nullslast(),
            Article.created_at.desc(),
        )
    elif sort_by == "latest":
        # Newest articles first — filter out articles older than 2 months
        from datetime import datetime, timedelta, timezone
        two_months_ago = datetime.now(timezone.utc) - timedelta(days=60)
        query = query.where(Article.created_at >= two_months_ago)
        query = query.order_by(
            Article.created_at.desc(),
        )
    elif sort_by == "impact":
        query = query.order_by(
            Article.priority_score.desc().nullslast(),
            Article.created_at.desc(),
        )
    else:  # recency
        query = query.order_by(Article.created_at.desc())

    query = query.limit(limit).offset(offset)

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
            select(Company.id, Company.name).where(
                Company.id.in_(company_ids),
                Company.tenant_id == ctx.tenant_id,
            )
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
        # Stage 3.5: Parse framework strings into enriched FrameworkHit objects
        fw_hits = [_parse_framework_tag(fw) for fw in merged]

        # GAP-7: Derive content_label from scoring_metadata or relationship_type
        scoring_meta = s.scoring_metadata or {}
        rel_type = chain.relationship_type if chain else "directOperational"
        content_label = scoring_meta.get("content_label")
        if not content_label:
            # Backward-compat: derive from relationship_type for older records
            if rel_type in ("directOperational", "geographicProximity", "climateRiskExposure"):
                content_label = "direct_impact"
            elif rel_type == "competitiveIntelligence":
                content_label = "competitive_intelligence"
            elif rel_type == "industrySpillover":
                content_label = "sector_news"
            else:
                content_label = "direct_impact"

        score_resp = ArticleScoreResponse(
            company_id=s.company_id,
            company_name=company_names.get(s.company_id, "Unknown"),
            impact_score=s.impact_score,
            causal_hops=s.causal_hops,
            relationship_type=rel_type,
            content_label=content_label,
            explanation=chain.explanation if chain else None,
            financial_exposure=s.financial_exposure,
            frameworks=merged,
            framework_hits=fw_hits,
            chain_path=chain.chain_path if chain else None,
            confidence=chain.confidence if chain else None,
            framework_alignment=chain_frameworks,
        )
        scores_by_article.setdefault(s.article_id, []).append(score_resp)

    response_articles = []
    for a in articles:
        article_frameworks = sorted(frameworks_by_article.get(a.id, set()))
        # Stage 3.5: Build article-level framework_hits from all unique frameworks
        article_fw_hits = [_parse_framework_tag(fw) for fw in article_frameworks]

        response_articles.append(ArticleResponse(
            id=a.id,
            title=a.title,
            summary=a.summary,
            source=a.source,
            url=a.url,
            image_url=a.image_url,
            published_at=a.published_at,
            esg_pillar=a.esg_pillar,
            sentiment=a.sentiment,
            entities=[
                e["text"] if isinstance(e, dict) else str(e)
                for e in (a.entities if isinstance(a.entities, list) else [])
            ],
            impact_scores=sorted(
                scores_by_article.get(a.id, []),
                key=lambda x: x.impact_score,
                reverse=True,
            ),
            predictions=preds_by_article.get(a.id, []),
            frameworks=article_frameworks,
            framework_hits=article_fw_hits,
            # Phase 1C: Enhanced fields for frontend
            sentiment_score=a.sentiment_score,
            sentiment_confidence=a.sentiment_confidence,
            aspect_sentiments=a.aspect_sentiments,
            content_type=a.content_type,
            urgency=a.urgency,
            time_horizon=a.time_horizon,
            reversibility=a.reversibility,
            priority_score=a.priority_score,
            priority_level=a.priority_level,
            financial_signal=a.financial_signal,
            executive_insight=a.executive_insight,
            relevance_score=a.relevance_score,
            relevance_breakdown=a.relevance_breakdown,
            deep_insight=a.deep_insight,
            rereact_recommendations=a.rereact_recommendations,
            # v2.0 Intelligence Modules
            nlp_extraction=a.nlp_extraction,
            esg_themes=a.esg_themes,
            framework_matches=a.framework_matches,
            risk_matrix=a.risk_matrix,
            geographic_signal=a.geographic_signal,
            # Internal fields for role-based scoring (not in JSON response)
            _content_type=a.content_type,
            _esg_pillar=a.esg_pillar,
            _topics=a.topics,
            _priority_score=a.priority_score,
            _published_at=a.published_at,
            _sentiment_score=a.sentiment_score,
        ))
    return response_articles


@router.get("/home", response_model=NewsFeedResponse)
async def get_home_articles(
    ctx: TenantContext = Depends(get_tenant_context),
) -> NewsFeedResponse:
    """Get HOME-tier articles (top 5 by priority, relevance >= 7).

    QA Audit 7: Dedicated endpoint enforcing 3-5 article limit server-side.
    Per v2.0 Module 10: negative-sentiment-first on ties.
    """
    articles = await _load_articles_with_scores(
        ctx, limit=5, offset=0, sort_by="priority",
    )
    # Filter to HOME-tier only (relevance >= 7)
    home_articles = [a for a in articles if (a.relevance_score or 0) >= 7]
    # Sort: priority desc, then negative sentiment first on ties
    home_articles.sort(
        key=lambda a: (-(a.priority_score or 0), (a.sentiment_score or 0)),
    )
    return NewsFeedResponse(articles=home_articles[:5], total=len(home_articles))


@router.get("/feed", response_model=NewsFeedResponse)
async def get_news_feed(
    company_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort_by: str = "priority",
    pillar: str | None = None,
    content_type: str | None = None,
    min_relevance: float = 20.0,  # GAP-7: filter out low-relevance noise (0-100 scale)
    ctx: TenantContext = Depends(get_tenant_context),
) -> NewsFeedResponse:
    """Get tenant-scoped news feed with impact scores.

    Phase 2C: Supports sort_by (priority|recency|impact), pillar filter (E|S|G),
    and content_type filter (regulatory|financial|operational|...).
    GAP-7: min_relevance filters out articles where all impact_scores are below threshold.
    """
    # Cap limit to prevent unbounded queries
    limit = min(limit, 200)
    articles = await _load_articles_with_scores(
        ctx, limit, offset,
        sort_by=sort_by,
        pillar_filter=pillar,
        content_type_filter=content_type,
    )

    # GAP-7: Filter out articles where the best impact score for any company
    # is below the min_relevance threshold — reduces cross-entity noise
    if min_relevance > 0 and articles:
        filtered = []
        for a in articles:
            if not a.impact_scores:
                # Keep articles without scores (unscored articles pass through)
                filtered.append(a)
                continue
            best_score = max(s.impact_score for s in a.impact_scores)
            if best_score >= min_relevance:
                filtered.append(a)
        articles = filtered

    # Phase 2C: Apply role-based re-scoring for personalized feed
    if sort_by == "priority" and articles:
        from backend.core.permissions import map_designation_to_role
        from backend.services.role_curation import (
            compute_role_relevance,
            compute_user_preference_boost,
            get_role_profile,
            recency_score,
        )

        user_role = map_designation_to_role(ctx.user.designation or "")
        # BUG-11: Pre-compute role profile ONCE before the loop
        role_profile = get_role_profile(user_role)

        # Load user preferences if they exist
        from sqlalchemy import select as sa_select
        from backend.models.user_preference import UserPreference
        pref_result = await ctx.db.execute(
            sa_select(UserPreference).where(
                UserPreference.user_id == ctx.user.user_id,
                UserPreference.tenant_id == ctx.tenant_id,
            )
        )
        user_pref = pref_result.scalar_one_or_none()

        # Score each article with role + preference boost
        scored = []
        for article_resp in articles:
            role_rel = compute_role_relevance(
                user_role,
                getattr(article_resp, "_content_type", None),
                article_resp.frameworks,
                getattr(article_resp, "_esg_pillar", None),
                role_profile=role_profile,
            )
            user_boost = 0.0
            if user_pref:
                user_boost = compute_user_preference_boost(
                    user_pref.preferred_frameworks,
                    user_pref.preferred_pillars,
                    user_pref.preferred_topics,
                    user_pref.dismissed_topics,
                    article_resp.frameworks,
                    getattr(article_resp, "_esg_pillar", None),
                    getattr(article_resp, "_topics", None),
                )
            # Composite feed score
            priority = getattr(article_resp, "_priority_score", None) or 0.0
            impact = max((s.impact_score for s in (article_resp.impact_scores or [])), default=0.0)
            rec = recency_score(getattr(article_resp, "_published_at", None))

            feed_score = (
                priority * 0.35
                + role_rel * 0.25
                + rec * 0.20
                + max(0, user_boost) * 0.15
                + impact * 0.05
            )
            # Sentiment for tie-breaking: negative sentiment first (downside risk priority)
            sentiment = getattr(article_resp, "_sentiment_score", None) or 0.5
            scored.append((feed_score, sentiment, article_resp))

        # Sort: highest feed_score first; on ties, most negative sentiment first
        scored.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        articles = [a for _, _, a in scored]

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


# --- Stage 8.5: API additions for frontend ---

class NewsStatsResponse(BaseModel):
    """FOMO metrics for IntroCard."""
    total: int
    high_impact_count: int
    new_last_24h: int
    predictions_count: int


@router.get("/stats", response_model=NewsStatsResponse)
async def get_news_stats(
    ctx: TenantContext = Depends(get_tenant_context),
) -> NewsStatsResponse:
    """Stage 8.5: News stats for IntroCard FOMO metrics.

    Returns total articles, high-impact count (score >70),
    new articles in last 24h, and active predictions.
    """
    # Total articles for tenant
    total_result = await ctx.db.execute(
        select(func.count(Article.id)).where(Article.tenant_id == ctx.tenant_id)
    )
    total = total_result.scalar() or 0

    # High impact: articles with any score > 70
    high_impact_result = await ctx.db.execute(
        select(func.count(func.distinct(ArticleScore.article_id))).where(
            ArticleScore.tenant_id == ctx.tenant_id,
            ArticleScore.impact_score > 70,
        )
    )
    high_impact_count = high_impact_result.scalar() or 0

    # New in last 24 hours
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    new_result = await ctx.db.execute(
        select(func.count(Article.id)).where(
            Article.tenant_id == ctx.tenant_id,
            Article.created_at >= cutoff_24h,
        )
    )
    new_last_24h = new_result.scalar() or 0

    # Active predictions
    predictions_result = await ctx.db.execute(
        select(func.count(PredictionReport.id)).where(
            PredictionReport.tenant_id == ctx.tenant_id,
            PredictionReport.status.in_(["completed", "pending"]),
        )
    )
    predictions_count = predictions_result.scalar() or 0

    return NewsStatsResponse(
        total=total,
        high_impact_count=high_impact_count,
        new_last_24h=new_last_24h,
        predictions_count=predictions_count,
    )


class BookmarkRequest(BaseModel):
    bookmarked: bool = True


@router.post("/{article_id}/bookmark")
async def bookmark_article(
    article_id: str,
    req: BookmarkRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Stage 8.5: Persist article bookmark server-side.

    Sets/clears bookmarked flag on the article for the current tenant.
    """
    result = await ctx.db.execute(
        select(Article).where(
            Article.id == article_id,
            Article.tenant_id == ctx.tenant_id,
        )
    )
    article = result.scalar_one_or_none()

    if not article:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Article not found",
        )

    article.bookmarked = req.bookmarked
    article.bookmarked_by = ctx.user.user_id if req.bookmarked else None
    try:
        await ctx.db.commit()
    except Exception:
        # BUG-13: Handle race condition — concurrent bookmark attempts
        # Rollback and re-read the current state (idempotent behavior)
        await ctx.db.rollback()
        result = await ctx.db.execute(
            select(Article).where(
                Article.id == article_id,
                Article.tenant_id == ctx.tenant_id,
            )
        )
        article = result.scalar_one_or_none()
        if article:
            article.bookmarked = req.bookmarked
            article.bookmarked_by = ctx.user.user_id if req.bookmarked else None
            await ctx.db.commit()

    logger.info(
        "article_bookmark_toggled",
        article_id=article_id,
        bookmarked=req.bookmarked,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user.user_id,
    )

    return {
        "article_id": article_id,
        "bookmarked": req.bookmarked,
    }
