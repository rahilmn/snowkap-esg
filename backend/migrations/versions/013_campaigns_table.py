"""Add content_campaigns table for newsletter/content generation storage.

Revision ID: 013_content_campaigns_table
Revises: 012_company_cap_listing_hq
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013_content_campaigns_table"
down_revision: Union[str, None] = "012_company_cap_listing_hq"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "content_campaigns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("topic", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), server_default="draft"),
        sa.Column("frameworks_referenced", sa.ARRAY(sa.String()), nullable=True),
        sa.Column("articles_used", sa.Integer(), server_default="0"),
        sa.Column("extra_data", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("content_campaigns")
