"""Add user_preferences table.

Phase 2A: Per-user feed customization.

Revision ID: 003_user_preferences
Revises: 002_sentiment_priority
Create Date: 2026-03-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "003_user_preferences"
down_revision: Union[str, None] = "002_sentiment_priority"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, unique=True, index=True),
        sa.Column("preferred_frameworks", ARRAY(sa.String), nullable=True),
        sa.Column("preferred_pillars", ARRAY(sa.String), nullable=True),
        sa.Column("preferred_topics", ARRAY(sa.String), nullable=True),
        sa.Column("alert_threshold", sa.Integer, default=70),
        sa.Column("content_depth", sa.String(20), default="standard"),
        sa.Column("companies_of_interest", ARRAY(sa.String), nullable=True),
        sa.Column("dismissed_topics", ARRAY(sa.String), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
