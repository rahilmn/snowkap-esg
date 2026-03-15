"""News article, scoring, and causal chain models.

Per MASTER_BUILD_PLAN:
- Layer 1: News Ingestion & Classification
- Layer 2: Entity Extraction & Linking
- Layer 4: Impact Propagation (CausalChain)
"""

from sqlalchemy import Float, ForeignKey, Integer, String, Text
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
