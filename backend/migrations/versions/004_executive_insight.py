"""Add executive_insight column to articles.

Phase B1: AI-generated CXO-level insight per article.

Revision ID: 004_executive_insight
Revises: 003_user_preferences
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004_executive_insight"
down_revision: Union[str, None] = "003_user_preferences"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("executive_insight", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("articles", "executive_insight")
