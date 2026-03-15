"""Base model and TenantMixin.

Per CLAUDE.md:
- Every table has tenant_id — enforced via TenantMixin
- Row-level tenant isolation
- tenants table = Snowkap customers
- companies table = ESG analysis targets (not tenants)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all models."""
    pass


class TimestampMixin:
    """Adds created_at and updated_at to any model."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )


class TenantMixin(TimestampMixin):
    """Mixin that adds tenant_id to every tenant-scoped table.

    Per CLAUDE.md Rule #1: NEVER return data from Tenant A to Tenant B.
    Every query MUST filter by tenant_id.
    """
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )


def generate_uuid() -> str:
    """Generate a UUID4 string for primary keys."""
    return str(uuid.uuid4())
