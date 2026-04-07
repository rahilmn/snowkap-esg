"""Campaign model — newsletter, peer comparison, leadership content.

Stores generated campaign content for history, editing, and scheduled delivery.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TenantMixin, generate_uuid


class ContentCampaign(Base, TenantMixin):
    """A generated content campaign (newsletter, peer report, disclosure draft, etc.)."""
    __tablename__ = "content_campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # newsletter, peer_comparison, leadership_brief, disclosure_draft
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft, scheduled, sent
    frameworks_referenced: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=list)
    articles_used: Mapped[int] = mapped_column(Integer, default=0)
    extra_data: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(36))  # user_id
