"""Add relevance scoring + deep insight + REREACT fields to articles.

Phase 1+2+3: 5D relevance score, 7-section deep insight, validated recommendations.

Revision ID: 007_relevance_deep_insight
Revises: 006_competitors
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "007_relevance_deep_insight"
down_revision: Union[str, None] = "006_competitors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Phase 1: 5D relevance scoring
    op.add_column("articles", sa.Column("relevance_score", sa.Float, nullable=True))
    op.add_column("articles", sa.Column("relevance_breakdown", JSONB, nullable=True))

    # Phase 2: Deep 7-section insight
    op.add_column("articles", sa.Column("deep_insight", JSONB, nullable=True))

    # Phase 3: REREACT validated recommendations
    op.add_column("articles", sa.Column("rereact_recommendations", JSONB, nullable=True))

    # Index for home feed filtering (relevance >= 7)
    op.create_index("ix_articles_relevance_score", "articles", ["relevance_score"])


def downgrade() -> None:
    op.drop_index("ix_articles_relevance_score")
    op.drop_column("articles", "rereact_recommendations")
    op.drop_column("articles", "deep_insight")
    op.drop_column("articles", "relevance_breakdown")
    op.drop_column("articles", "relevance_score")
