"""Company, Facility, Supplier models — ESG analysis targets.

Per CLAUDE.md:
- companies table = ESG analysis targets (NOT tenants)
- Every table has tenant_id via TenantMixin
"""

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TenantMixin, generate_uuid


class Company(Base, TenantMixin):
    """An ESG analysis target company — NOT a Snowkap customer (that's Tenant)."""
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    domain: Mapped[str | None] = mapped_column(String(255))
    industry: Mapped[str | None] = mapped_column(String(255))
    sasb_category: Mapped[str | None] = mapped_column(String(255))
    kpi_profile: Mapped[str | None] = mapped_column(Text)
    sustainability_query: Mapped[str | None] = mapped_column(Text)
    general_query: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default="active")
    profile_data: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    competitors: Mapped[dict | None] = mapped_column(JSONB)  # [{name, domain, relationship, sub_sector}]

    # Relationships
    facilities: Mapped[list["Facility"]] = relationship(back_populates="company")
    suppliers: Mapped[list["Supplier"]] = relationship(
        back_populates="company", foreign_keys="Supplier.company_id"
    )


class Facility(Base, TenantMixin):
    """Physical facility/plant location for geographic intelligence.

    Per MASTER_BUILD_PLAN Phase 3.3: Geographic Intelligence
    - Company → facility locations (lat/lng, district, state)
    - Proximity matching for news impact
    """
    __tablename__ = "facilities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    facility_type: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(255))
    district: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str] = mapped_column(String(100), default="India")
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    climate_risk_zone: Mapped[str | None] = mapped_column(String(100))

    # Relationships
    company: Mapped["Company"] = relationship(back_populates="facilities")


class Supplier(Base, TenantMixin):
    """Supply chain link for Scope 3 and causal chain analysis.

    Per MASTER_BUILD_PLAN Phase 3.4: Supply Chain Graph
    - Company → Tier 1 suppliers
    - Commodity dependency mapping
    - Scope 3 category linkage
    """
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    supplier_name: Mapped[str] = mapped_column(String(255), nullable=False)
    supplier_domain: Mapped[str | None] = mapped_column(String(255))
    tier: Mapped[int] = mapped_column(default=1)
    commodity: Mapped[str | None] = mapped_column(String(255))
    relationship_type: Mapped[str] = mapped_column(String(100), default="supplyChainUpstream")
    scope3_category: Mapped[str | None] = mapped_column(String(100))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)

    # Relationships
    company: Mapped["Company"] = relationship(back_populates="suppliers")
