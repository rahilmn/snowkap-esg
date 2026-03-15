"""Seed script — populate 2 test tenants + company data.

Per MASTER_BUILD_PLAN Phase 2:
- Seed script with 2 test tenants + company data
- Critical split: tenants (Snowkap customers) vs companies (ESG targets)

Usage: python -m backend.scripts.seed
"""

import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import async_session_factory, engine
from backend.models.base import Base
from backend.models.company import Company, Facility, Supplier
from backend.models.tenant import Tenant, TenantConfig, TenantMembership
from backend.models.user import User


def uid() -> str:
    return str(uuid.uuid4())


TENANT_A_ID = "t-00000001-aaaa-bbbb-cccc-000000000001"
TENANT_B_ID = "t-00000002-aaaa-bbbb-cccc-000000000002"
USER_A_ID = "u-00000001-aaaa-bbbb-cccc-000000000001"
USER_B_ID = "u-00000002-aaaa-bbbb-cccc-000000000002"


async def seed() -> None:
    """Create test data: 2 tenants, 2 users, companies, facilities, suppliers."""

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Enable pgvector extension
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    async with async_session_factory() as db:
        # ---- Tenant A: Mahindra Logistics ----
        tenant_a = Tenant(
            id=TENANT_A_ID,
            name="Mahindra Logistics Ltd",
            domain="mahindra.com",
            industry="Transportation",
            sasb_category="Transportation",
            sustainability_query='"Mahindra Logistics" ESG sustainability carbon emissions',
            general_query='"Mahindra Logistics" OR "Mahindra Group" business news',
        )
        db.add(tenant_a)

        user_a = User(
            id=USER_A_ID,
            email="sustainability@mahindra.com",
            domain="mahindra.com",
            name="Priya Sharma",
            designation="Head of Sustainability",
        )
        db.add(user_a)

        membership_a = TenantMembership(
            tenant_id=TENANT_A_ID,
            user_id=USER_A_ID,
            role="sustainability_manager",
            designation="Head of Sustainability",
            permissions=[
                "view_dashboard", "view_news", "view_analysis", "view_predictions",
                "edit_analysis", "verify_reports", "manage_ontology", "trigger_predictions",
                "manage_campaigns",
            ],
        )
        db.add(membership_a)

        config_a = TenantConfig(
            tenant_id=TENANT_A_ID,
            workflow_stages={"stages": ["draft", "review", "approved", "published"]},
            business_rules={"auto_analyze": True, "min_impact_score": 30},
            mirofish_config={"agent_count": 20, "max_rounds": 10},
        )
        db.add(config_a)

        # Companies tracked by Tenant A
        mahindra = Company(
            tenant_id=TENANT_A_ID,
            name="Mahindra Logistics Ltd",
            slug="mahindra-logistics",
            domain="mahindra.com",
            industry="Transportation",
            sasb_category="Transportation",
            sustainability_query='"Mahindra Logistics" ESG sustainability',
            general_query='"Mahindra Logistics" news',
        )
        db.add(mahindra)
        await db.flush()

        # Facilities for Mahindra
        db.add(Facility(
            tenant_id=TENANT_A_ID, company_id=mahindra.id,
            name="Mumbai HQ", facility_type="headquarters",
            city="Mumbai", district="Mumbai", state="Maharashtra", country="India",
            latitude=19.076, longitude=72.8777,
        ))
        db.add(Facility(
            tenant_id=TENANT_A_ID, company_id=mahindra.id,
            name="Kolhapur Warehouse", facility_type="warehouse",
            city="Kolhapur", district="Kolhapur", state="Maharashtra", country="India",
            latitude=16.7050, longitude=74.2433, climate_risk_zone="flood_prone",
        ))

        # Suppliers for Mahindra
        db.add(Supplier(
            tenant_id=TENANT_A_ID, company_id=mahindra.id,
            supplier_name="Indian Oil Corporation", supplier_domain="iocl.com",
            tier=1, commodity="Diesel", relationship_type="supplyChainUpstream",
            scope3_category="Category 1: Purchased goods",
        ))
        db.add(Supplier(
            tenant_id=TENANT_A_ID, company_id=mahindra.id,
            supplier_name="Tata Steel", supplier_domain="tatasteel.com",
            tier=2, commodity="Steel", relationship_type="supplyChainUpstream",
            scope3_category="Category 1: Purchased goods",
        ))

        # ---- Tenant B: Infosys ----
        tenant_b = Tenant(
            id=TENANT_B_ID,
            name="Infosys Limited",
            domain="infosys.com",
            industry="Software & IT Services",
            sasb_category="Software & IT Services",
            sustainability_query='"Infosys" ESG sustainability carbon neutral',
            general_query='"Infosys" business news technology',
        )
        db.add(tenant_b)

        user_b = User(
            id=USER_B_ID,
            email="esg@infosys.com",
            domain="infosys.com",
            name="Rahul Verma",
            designation="ESG Manager",
        )
        db.add(user_b)

        membership_b = TenantMembership(
            tenant_id=TENANT_B_ID,
            user_id=USER_B_ID,
            role="sustainability_manager",
            designation="ESG Manager",
            permissions=[
                "view_dashboard", "view_news", "view_analysis", "view_predictions",
                "edit_analysis", "verify_reports", "manage_ontology", "trigger_predictions",
            ],
        )
        db.add(membership_b)

        config_b = TenantConfig(
            tenant_id=TENANT_B_ID,
            workflow_stages={"stages": ["intake", "analysis", "review", "final"]},
            business_rules={"auto_analyze": True, "min_impact_score": 40},
        )
        db.add(config_b)

        # Companies tracked by Tenant B
        infosys = Company(
            tenant_id=TENANT_B_ID,
            name="Infosys Limited",
            slug="infosys",
            domain="infosys.com",
            industry="Software & IT Services",
            sasb_category="Software & IT Services",
            sustainability_query='"Infosys" ESG sustainability',
            general_query='"Infosys" news',
        )
        db.add(infosys)
        await db.flush()

        db.add(Facility(
            tenant_id=TENANT_B_ID, company_id=infosys.id,
            name="Bangalore Campus", facility_type="campus",
            city="Bangalore", district="Bangalore Urban", state="Karnataka", country="India",
            latitude=12.9716, longitude=77.5946,
        ))
        db.add(Facility(
            tenant_id=TENANT_B_ID, company_id=infosys.id,
            name="Pune DC", facility_type="data_center",
            city="Pune", district="Pune", state="Maharashtra", country="India",
            latitude=18.5204, longitude=73.8567, climate_risk_zone="water_stress",
        ))

        await db.commit()

    print("Seed complete: 2 tenants, 2 users, 2 companies, 4 facilities, 2 suppliers")


if __name__ == "__main__":
    asyncio.run(seed())
