"""User-level preferences for news feed personalization.

Phase 2A: Overrides role-based defaults. Stored per user per tenant.
"""

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TenantMixin, generate_uuid


class UserPreference(Base, TenantMixin):
    """Per-user feed customization preferences."""
    __tablename__ = "user_preferences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, unique=True, index=True,
    )

    # Framework preferences — articles matching these get boosted
    preferred_frameworks: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=list)

    # ESG pillar preferences — ["E", "S", "G"] or subset
    preferred_pillars: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=list)

    # Topic preferences — ["emissions", "water", "governance", "supply_chain"]
    preferred_topics: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=list)

    # Priority threshold for push notifications (0-100)
    alert_threshold: Mapped[int] = mapped_column(Integer, default=70)

    # Content depth preference: brief / standard / detailed
    content_depth: Mapped[str] = mapped_column(String(20), default="standard")

    # Specific companies to prioritize
    companies_of_interest: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=list)

    # Topics to suppress from feed
    dismissed_topics: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=list)
