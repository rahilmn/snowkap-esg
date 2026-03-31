"""News article, scoring, and causal chain models.

Per MASTER_BUILD_PLAN:
- Layer 1: News Ingestion & Classification
- Layer 2: Entity Extraction & Linking
- Layer 4: Impact Propagation (CausalChain)
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TenantMixin, generate_uuid


class Article(Base, TenantMixin):
    """A news article ingested via Google News RSS or NewsAPI."""
    __tablename__ = "articles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(100))
    sentiment: Mapped[str | None] = mapped_column(String(50))
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    entities: Mapped[dict | None] = mapped_column(JSONB, default=list)
    topics: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=list)
    esg_pillar: Mapped[str | None] = mapped_column(String(50))

    # Phase 1C: Sentiment depth
    sentiment_confidence: Mapped[float | None] = mapped_column(Float)
    aspect_sentiments: Mapped[dict | None] = mapped_column(JSONB)

    # Phase 1C: Content classification
    content_type: Mapped[str | None] = mapped_column(String(50))

    # Phase 1C: Criticality assessment
    urgency: Mapped[str | None] = mapped_column(String(20))
    time_horizon: Mapped[str | None] = mapped_column(String(20))
    reversibility: Mapped[str | None] = mapped_column(String(20))
    stakeholder_impact: Mapped[list[str] | None] = mapped_column(ARRAY(String))

    # Phase 1C: Structured financial signal
    financial_signal: Mapped[dict | None] = mapped_column(JSONB)
    regulatory_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Phase 1E: Composite priority
    priority_score: Mapped[float | None] = mapped_column(Float, index=True)
    priority_level: Mapped[str | None] = mapped_column(String(20))

    # Phase B1: AI-generated executive insight
    executive_insight: Mapped[str | None] = mapped_column(Text)

    # Phase 4: Climate events detected in article
    climate_events: Mapped[list[str] | None] = mapped_column(ARRAY(String))

    # Advanced Intelligence: 5D relevance scoring (Phase 1)
    relevance_score: Mapped[float | None] = mapped_column(Float)
    relevance_breakdown: Mapped[dict | None] = mapped_column(JSONB)

    # Advanced Intelligence: 7-section deep insight (Phase 2)
    deep_insight: Mapped[dict | None] = mapped_column(JSONB)

    # Advanced Intelligence: REREACT validated recommendations (Phase 3)
    rereact_recommendations: Mapped[dict | None] = mapped_column(JSONB)

    # v2.0 Module 1: NLP Narrative & Tone Extraction
    nlp_extraction: Mapped[dict | None] = mapped_column(JSONB)

    # v2.0 Module 3: ESG Theme Tags (primary + secondary themes with sub-metrics)
    esg_themes: Mapped[dict | None] = mapped_column(JSONB)

    # v2.0 Module 4: Framework RAG matches (applicable frameworks with section citations)
    framework_matches: Mapped[dict | None] = mapped_column(JSONB)

    # v2.0 Module 6: 10-Category Risk Taxonomy (probability × exposure matrix)
    risk_matrix: Mapped[dict | None] = mapped_column(JSONB)

    # v2.0 Module 2: Geographic Intelligence (structured geo signal)
    geographic_signal: Mapped[dict | None] = mapped_column(JSONB)

    # GAP 8: Event deduplication — cluster metadata (primary article, related IDs, consolidated scores)
    scoring_metadata: Mapped[dict | None] = mapped_column(JSONB, default=dict)


class ArticleScore(Base, TenantMixin):
    """Relevance score of an article to a specific company."""
    __tablename__ = "article_scores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    article_id: Mapped[str] = mapped_column(String(36), ForeignKey("articles.id"), nullable=False, index=True)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    impact_score: Mapped[float] = mapped_column(Float, default=0.0)
    financial_exposure: Mapped[float | None] = mapped_column(Float)
    causal_hops: Mapped[int] = mapped_column(Integer, default=0)
    frameworks: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=list)
    scoring_metadata: Mapped[dict | None] = mapped_column(JSONB, default=dict)

    # Phase 2C: Role-based relevance scoring
    role_relevance_score: Mapped[float | None] = mapped_column(Float)


class CausalChain(Base, TenantMixin):
    """A causal chain linking a news event to a company via the ontology graph.

    Per MASTER_BUILD_PLAN Part 1: Causal Chain Architecture
    - BFS/DFS from news entity to company node, max 4 hops
    - Impact scoring: decay per hop (1.0 → 0.7 → 0.4 → 0.2)
    - Human-readable path explanation
    """
    __tablename__ = "causal_chains"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    article_id: Mapped[str] = mapped_column(String(36), ForeignKey("articles.id"), nullable=False, index=True)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    chain_path: Mapped[list | None] = mapped_column(JSONB, nullable=False)
    hops: Mapped[int] = mapped_column(Integer, nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(100), nullable=False)
    impact_score: Mapped[float] = mapped_column(Float, nullable=False)
    financial_estimate: Mapped[float | None] = mapped_column(Float)
    explanation: Mapped[str | None] = mapped_column(Text)
    esg_pillar: Mapped[str | None] = mapped_column(String(50))
    framework_alignment: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
