"""Add competitors JSONB field to companies.

Revision ID: 006_competitors
Revises: 005_climate_events
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "006_competitors"
down_revision: Union[str, None] = "005_climate_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("competitors", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "competitors")
