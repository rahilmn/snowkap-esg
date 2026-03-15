"""Ontology models — business rules, assertions, inference log.

Per MASTER_BUILD_PLAN Phase 3.5: Tenant Business Rules as OWL Axioms
- BusinessRuleCompiler: tenant rules → OWL axioms → Jena named graph
- Permission-gated: admin creates rules, users assert facts
"""

from sqlalchemy import Boolean, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TenantMixin, generate_uuid


class OntologyRule(Base, TenantMixin):
    """A business rule that compiles to OWL axioms in the tenant's Jena named graph."""
    __tablename__ = "ontology_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    rule_type: Mapped[str] = mapped_column(String(100), nullable=False)
    condition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    action: Mapped[dict] = mapped_column(JSONB, nullable=False)
    owl_axiom: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(36))


class Assertion(Base, TenantMixin):
    """A human-asserted fact in the ontology (domain-specific classification)."""
    __tablename__ = "assertions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    subject_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    predicate_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    object_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    assertion_type: Mapped[str] = mapped_column(String(100), default="human")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    asserted_by: Mapped[str | None] = mapped_column(String(36))
    source: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class InferenceLog(Base, TenantMixin):
    """Log of ontology inferences — auto-derived vs human-asserted tracking."""
    __tablename__ = "inference_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    rule_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("ontology_rules.id"))
    inference_type: Mapped[str] = mapped_column(String(100), nullable=False)
    input_data: Mapped[dict | None] = mapped_column(JSONB)
    output_triples: Mapped[list | None] = mapped_column(JSONB)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(50), default="completed")
