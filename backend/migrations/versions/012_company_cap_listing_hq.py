"""Add market_cap, listing_exchange, headquarter_country, headquarter_region to companies.

Revision ID: 012_company_cap_listing_hq
Revises: 011_gap9_banking_es_queries
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "012_company_cap_listing_hq"
down_revision: Union[str, None] = "011_gap9_banking_es_queries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Backfill data for existing companies
COMPANY_DATA = [
    {"name": "Nike", "market_cap": "Large Cap", "listing_exchange": "NYSE", "headquarter_country": "USA", "headquarter_region": "North America"},
    {"name": "ICICI Bank", "market_cap": "Large Cap", "listing_exchange": "NSE/BSE", "headquarter_country": "India", "headquarter_region": "Asia-Pacific"},
    {"name": "YES Bank", "market_cap": "Mid Cap", "listing_exchange": "NSE/BSE", "headquarter_country": "India", "headquarter_region": "Asia-Pacific"},
    {"name": "IDFC First Bank", "market_cap": "Mid Cap", "listing_exchange": "NSE/BSE", "headquarter_country": "India", "headquarter_region": "Asia-Pacific"},
    {"name": "Waaree Energies", "market_cap": "Mid Cap", "listing_exchange": "NSE/BSE", "headquarter_country": "India", "headquarter_region": "Asia-Pacific"},
    {"name": "Singularity AMC", "market_cap": "Small Cap", "listing_exchange": "unlisted", "headquarter_country": "India", "headquarter_region": "Asia-Pacific"},
    {"name": "Adani Power", "market_cap": "Large Cap", "listing_exchange": "NSE/BSE", "headquarter_country": "India", "headquarter_region": "Asia-Pacific"},
    {"name": "JSW Energy", "market_cap": "Large Cap", "listing_exchange": "NSE/BSE", "headquarter_country": "India", "headquarter_region": "Asia-Pacific"},
]


def upgrade() -> None:
    # Add the 4 new columns
    op.add_column("companies", sa.Column("market_cap", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("listing_exchange", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("headquarter_country", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("headquarter_region", sa.String(), nullable=True))

    # Backfill existing companies
    for c in COMPANY_DATA:
        op.execute(
            f"""UPDATE companies
                SET market_cap = '{c["market_cap"]}',
                    listing_exchange = '{c["listing_exchange"]}',
                    headquarter_country = '{c["headquarter_country"]}',
                    headquarter_region = '{c["headquarter_region"]}'
                WHERE name ILIKE '%%{c["name"]}%%'"""
        )


def downgrade() -> None:
    op.drop_column("companies", "headquarter_region")
    op.drop_column("companies", "headquarter_country")
    op.drop_column("companies", "listing_exchange")
    op.drop_column("companies", "market_cap")
