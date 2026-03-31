"""GAP 9: Add Environmental/Social terms to banking tenant queries.

Banking and AMC tenants (ICICI Bank, YES Bank, IDFC First Bank, Singularity AMC)
were only receiving Governance-themed articles because their sustainability_query
and general_query fields lacked E/S search terms.

This migration updates their queries to include balanced E, S, and G terms.

Revision ID: 011_gap9_banking_es_queries
Revises: 010_article_scoring_metadata
"""
from typing import Sequence, Union

from alembic import op

revision: str = "011_gap9_banking_es_queries"
down_revision: Union[str, None] = "010_article_scoring_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# GAP 9: Updated queries with balanced E/S/G terms for financial sector tenants
TENANT_UPDATES = [
    {
        "name": "ICICI Bank",
        "sustainability_query": (
            '"ICICI Bank" ESG sustainability financed emissions climate risk disclosure '
            'green bond sustainable finance green lending renewable energy financing'
        ),
        "general_query": (
            '"ICICI Bank" financial inclusion workforce diversity social impact '
            'community investment responsible lending ESG investing climate portfolio'
        ),
    },
    {
        "name": "YES Bank",
        "sustainability_query": (
            '"YES Bank" ESG sustainability financed emissions climate risk disclosure '
            'green bond sustainable finance green lending renewable energy financing'
        ),
        "general_query": (
            '"YES Bank" financial inclusion workforce diversity social impact '
            'community investment responsible lending ESG investing climate portfolio'
        ),
    },
    {
        "name": "IDFC First Bank",
        "sustainability_query": (
            '"IDFC First Bank" ESG sustainability financed emissions climate risk disclosure '
            'green bond sustainable finance green lending renewable energy financing'
        ),
        "general_query": (
            '"IDFC First Bank" financial inclusion workforce diversity social impact '
            'community investment responsible lending ESG investing climate portfolio'
        ),
    },
    {
        "name": "Singularity AMC",
        "sustainability_query": (
            '"Singularity AMC" ESG sustainability ESG investing responsible investment '
            'climate portfolio carbon footprint green fund sustainable finance'
        ),
        "general_query": (
            '"Singularity AMC" social impact investing workforce diversity '
            'financial inclusion community development responsible investment governance'
        ),
    },
]


def upgrade() -> None:
    for tenant in TENANT_UPDATES:
        # Update tenants table
        op.execute(
            f"""UPDATE tenants
                SET sustainability_query = '{tenant["sustainability_query"]}',
                    general_query = '{tenant["general_query"]}'
                WHERE name ILIKE '%{tenant["name"]}%'"""
        )
        # Also update companies table if the company exists there
        op.execute(
            f"""UPDATE companies
                SET sustainability_query = '{tenant["sustainability_query"]}',
                    general_query = '{tenant["general_query"]}'
                WHERE name ILIKE '%{tenant["name"]}%'"""
        )


def downgrade() -> None:
    # Revert to generic ESG-only queries
    for tenant in TENANT_UPDATES:
        name = tenant["name"]
        op.execute(
            f"""UPDATE tenants
                SET sustainability_query = '"{name}" ESG sustainability',
                    general_query = '"{name}" news'
                WHERE name ILIKE '%{name}%'"""
        )
        op.execute(
            f"""UPDATE companies
                SET sustainability_query = '"{name}" ESG sustainability',
                    general_query = '"{name}" news'
                WHERE name ILIKE '%{name}%'"""
        )
