"""ESG Analysis, Recommendation, and Framework models."""

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TenantMixin, generate_uuid


class Analysis(Base, TenantMixin):
    """An ESG analysis report for a company."""
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    analysis_type: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    esg_scores: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    frameworks: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=list)
    status: Mapped[str] = mapped_column(String(50), default="draft")
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)


class Recommendation(Base, TenantMixin):
    """Actionable ESG recommendation generated from analysis or causal chain."""
    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    analysis_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("analyses.id"))
    causal_chain_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("causal_chains.id"))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(50), default="medium")
    category: Mapped[str | None] = mapped_column(String(100))
    estimated_impact: Mapped[str | None] = mapped_column(Text)
    financial_estimate: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    frameworks: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=list)


class Framework(Base, TenantMixin):
    """ESG framework tracking per company (BRSR, ESRS, GRI, IFRS, CDP, TCFD, CSRD, SASB)."""
    __tablename__ = "frameworks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    framework_name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str | None] = mapped_column(String(50))
    compliance_score: Mapped[float | None] = mapped_column(Float)
    total_indicators: Mapped[int] = mapped_column(Integer, default=0)
    completed_indicators: Mapped[int] = mapped_column(Integer, default=0)
    disclosure_data: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    gaps: Mapped[list | None] = mapped_column(JSONB, default=list)
