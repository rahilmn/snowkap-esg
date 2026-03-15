"""Tenant models — Snowkap customers (NOT ESG analysis targets).

Per CLAUDE.md:
- tenants table = Snowkap customers (the multi-tenant split)
- companies table = ESG analysis targets (separate model)
- Auto-provision: first user from a new domain creates the tenant
"""

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin, generate_uuid


class Tenant(Base, TimestampMixin):
    """A Snowkap customer organization (multi-tenant root)."""
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    industry: Mapped[str | None] = mapped_column(String(255))
    sasb_category: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sustainability_query: Mapped[str | None] = mapped_column(Text)
    general_query: Mapped[str | None] = mapped_column(Text)

    # Relationships
    memberships: Mapped[list["TenantMembership"]] = relationship(back_populates="tenant")
    config: Mapped["TenantConfig | None"] = relationship(back_populates="tenant", uselist=False)


class TenantMembership(Base, TimestampMixin):
    """Links users to tenants with role information."""
    __tablename__ = "tenant_memberships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(100), nullable=False, default="member")
    designation: Mapped[str | None] = mapped_column(String(255))
    permissions: Mapped[dict | None] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship(back_populates="memberships")


class TenantConfig(Base, TimestampMixin):
    """Per-tenant configuration: workflow stages, custom fields, business rules.

    Stored as JSONB for flexibility — each tenant can customize their ESG workflow.
    """
    __tablename__ = "tenant_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False, unique=True)
    workflow_stages: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    custom_fields: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    business_rules: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    notification_settings: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    mirofish_config: Mapped[dict | None] = mapped_column(JSONB, default=dict)

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="config")


# Avoid circular import — referenced via string in relationship
from backend.models.user import User  # noqa: E402, F401
