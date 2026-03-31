"""Add sentiment depth, criticality, content type, priority fields.

Phase 1D: 11 new columns on articles + 1 on article_scores + indexes.

Revision ID: 002_sentiment_priority
Revises: 001_initial
Create Date: 2026-03-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "002_sentiment_priority"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Article: sentiment depth
    op.add_column("articles", sa.Column("sentiment_confidence", sa.Float, nullable=True))
    op.add_column("articles", sa.Column("aspect_sentiments", JSONB, nullable=True))

    # Article: content classification
    op.add_column("articles", sa.Column("content_type", sa.String(50), nullable=True))

    # Article: criticality assessment
    op.add_column("articles", sa.Column("urgency", sa.String(20), nullable=True))
    op.add_column("articles", sa.Column("time_horizon", sa.String(20), nullable=True))
    op.add_column("articles", sa.Column("reversibility", sa.String(20), nullable=True))
    op.add_column("articles", sa.Column("stakeholder_impact", ARRAY(sa.String), nullable=True))

    # Article: structured financial signal
    op.add_column("articles", sa.Column("financial_signal", JSONB, nullable=True))
    op.add_column("articles", sa.Column("regulatory_deadline", sa.DateTime(timezone=True), nullable=True))

    # Article: composite priority
    op.add_column("articles", sa.Column("priority_score", sa.Float, nullable=True))
    op.add_column("articles", sa.Column("priority_level", sa.String(20), nullable=True))

    # ArticleScore: role-based relevance
    op.add_column("article_scores", sa.Column("role_relevance_score", sa.Float, nullable=True))

    # Indexes for priority-based feed queries
    op.create_index("ix_articles_priority_score", "articles", ["priority_score"])
    op.create_index("ix_articles_content_type", "articles", ["content_type"])


def downgrade() -> None:
    op.drop_index("ix_articles_content_type")
    op.drop_index("ix_articles_priority_score")
    op.drop_column("article_scores", "role_relevance_score")
    op.drop_column("articles", "priority_level")
    op.drop_column("articles", "priority_score")
    op.drop_column("articles", "regulatory_deadline")
    op.drop_column("articles", "financial_signal")
    op.drop_column("articles", "stakeholder_impact")
    op.drop_column("articles", "reversibility")
    op.drop_column("articles", "time_horizon")
    op.drop_column("articles", "urgency")
    op.drop_column("articles", "content_type")
    op.drop_column("articles", "aspect_sentiments")
    op.drop_column("articles", "sentiment_confidence")
