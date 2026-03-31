"""Add climate_events field to articles.

Phase 4: Climate event detection for contextual intelligence.

Revision ID: 005_climate_events
Revises: 004_executive_insight
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "005_climate_events"
down_revision: Union[str, None] = "004_executive_insight"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("climate_events", ARRAY(sa.String), nullable=True))


def downgrade() -> None:
    op.drop_column("articles", "climate_events")
