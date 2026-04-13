"""Add financial fields to companies table.

Adds market_cap_value, revenue_last_fy, and employee_count columns.
These may already exist if backfill_company_data.py was run previously,
so we use IF NOT EXISTS to make this migration idempotent.

Revision ID: 015_company_financial_fields
Revises: 014_remove_nike
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "015_company_financial_fields"
down_revision: Union[str, None] = "014_remove_nike"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS market_cap_value FLOAT"))
    conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS revenue_last_fy FLOAT"))
    conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS employee_count INTEGER"))


def downgrade() -> None:
    op.drop_column("companies", "employee_count")
    op.drop_column("companies", "revenue_last_fy")
    op.drop_column("companies", "market_cap_value")
