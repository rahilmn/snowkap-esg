"""Add scoring_metadata JSONB column to articles table for event deduplication.

GAP 8: Event deduplication stores cluster metadata (primary article, related IDs,
consolidated risk scores) on each article in a cluster.

Revision ID: 010_article_scoring_metadata
Revises: 009_qa_indexes
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "010_article_scoring_metadata"
down_revision: Union[str, None] = "009_qa_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("scoring_metadata", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("articles", "scoring_metadata")
