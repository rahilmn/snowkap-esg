"""MiroFish prediction models.

Per CLAUDE.md:
- Triggered only on high-impact news (score >70, financial exposure >₹10L)
- 20-50 agents per simulation, 10-40 rounds
- Results stored in prediction_reports table + Jena triples
"""

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TenantMixin, generate_uuid


class PredictionReport(Base, TenantMixin):
    """MiroFish simulation prediction report."""
    __tablename__ = "prediction_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    article_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("articles.id"))
    causal_chain_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("causal_chains.id"))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    prediction_text: Mapped[str | None] = mapped_column(Text)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.5)
    financial_impact: Mapped[float | None] = mapped_column(Float)
    time_horizon: Mapped[str | None] = mapped_column(String(100))
    scenario_variables: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    agent_consensus: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(50), default="completed")


class SimulationRun(Base, TenantMixin):
    """Individual MiroFish simulation execution record."""
    __tablename__ = "simulation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    prediction_report_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("prediction_reports.id"), nullable=False, index=True
    )
    agent_count: Mapped[int] = mapped_column(Integer, default=20)
    rounds: Mapped[int] = mapped_column(Integer, default=10)
    seed_data: Mapped[dict | None] = mapped_column(JSONB)
    config: Mapped[dict | None] = mapped_column(JSONB)
    results: Mapped[dict | None] = mapped_column(JSONB)
    convergence_score: Mapped[float | None] = mapped_column(Float)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    error: Mapped[str | None] = mapped_column(Text)
