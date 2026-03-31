"""Add v2.0 module columns: NLP extraction, ESG themes, framework matches, risk matrix, geographic signal.

v2.0 Modules 1, 2, 3, 4, 6 — new JSONB columns on articles table.

Revision ID: 008_v2_modules
Revises: 007_relevance_deep_insight
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "008_v2_modules"
down_revision: Union[str, None] = "007_relevance_deep_insight"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("nlp_extraction", JSONB, nullable=True))
    op.add_column("articles", sa.Column("esg_themes", JSONB, nullable=True))
    op.add_column("articles", sa.Column("framework_matches", JSONB, nullable=True))
    op.add_column("articles", sa.Column("risk_matrix", JSONB, nullable=True))
    op.add_column("articles", sa.Column("geographic_signal", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("articles", "geographic_signal")
    op.drop_column("articles", "risk_matrix")
    op.drop_column("articles", "framework_matches")
    op.drop_column("articles", "esg_themes")
    op.drop_column("articles", "nlp_extraction")
