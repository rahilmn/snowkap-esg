"""Remove Nike data from the platform.

Nike was used as a test company but is no longer needed.
This migration removes all Nike-related records across tenant-scoped tables.

Revision ID: 014_remove_nike
Revises: 013_campaigns_table
"""
from typing import Sequence, Union

from alembic import op

revision: str = "014_remove_nike"
down_revision: Union[str, None] = "013_campaigns_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Delete in dependency order (child tables first)

    # 1. Article scores referencing Nike companies
    op.execute("""
        DELETE FROM article_scores
        WHERE company_id IN (SELECT id FROM companies WHERE name ILIKE '%%Nike%%')
    """)

    # 2. Causal chains referencing Nike companies
    op.execute("""
        DELETE FROM causal_chains
        WHERE company_id IN (SELECT id FROM companies WHERE name ILIKE '%%Nike%%')
    """)

    # 3. Suppliers of Nike companies
    op.execute("""
        DELETE FROM suppliers
        WHERE company_id IN (SELECT id FROM companies WHERE name ILIKE '%%Nike%%')
    """)

    # 4. Facilities of Nike companies
    op.execute("""
        DELETE FROM facilities
        WHERE company_id IN (SELECT id FROM companies WHERE name ILIKE '%%Nike%%')
    """)

    # 5. Nike companies themselves
    op.execute("""
        DELETE FROM companies WHERE name ILIKE '%%Nike%%'
    """)

    # 6. Users with nike.com domain
    op.execute("""
        DELETE FROM users WHERE domain = 'nike.com'
    """)

    # 7. Tenant memberships for Nike tenant
    op.execute("""
        DELETE FROM tenant_memberships
        WHERE tenant_id IN (SELECT id FROM tenants WHERE domain = 'nike.com')
    """)

    # 8. Nike tenant
    op.execute("""
        DELETE FROM tenants WHERE domain = 'nike.com'
    """)


def downgrade() -> None:
    # Data removal is intentional — no downgrade needed.
    pass
