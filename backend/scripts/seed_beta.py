"""Seed 7 beta companies with real data — facilities, suppliers, competitors, Jena graphs.

Usage: python -m backend.scripts.seed_beta
"""

import asyncio
import structlog
from sqlalchemy import select

from backend.core.database import async_session_factory
from backend.models.base import generate_uuid
from backend.models.tenant import Tenant, TenantMembership
from backend.models.user import User
from backend.models.company import Company, Facility, Supplier

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Beta company definitions
# ---------------------------------------------------------------------------

BETA_COMPANIES = [
    {
        "domain": "icicibank.com",
        "name": "ICICI Bank Ltd",
        "industry": "Financials",
        "sasb_category": "Commercial Banks",
        "sustainability_query": '"ICICI Bank" ESG sustainability RBI green finance 2026',
        "general_query": '"ICICI Bank" corporate governance responsibility 2026',
        "slug": "icici-bank",
        "competitors": [
            {"name": "HDFC Bank", "domain": "hdfcbank.com", "relationship": "direct", "sub_sector": "Private Banks"},
            {"name": "State Bank of India", "domain": "sbi.co.in", "relationship": "direct", "sub_sector": "Public Banks"},
            {"name": "Axis Bank", "domain": "axisbank.com", "relationship": "direct", "sub_sector": "Private Banks"},
            {"name": "Kotak Mahindra Bank", "domain": "kotak.com", "relationship": "direct", "sub_sector": "Private Banks"},
        ],
        "facilities": [
            {"name": "ICICI Towers BKC", "type": "headquarters", "city": "Mumbai", "state": "Maharashtra", "country": "India", "lat": 19.0658, "lng": 72.8686, "climate_risk": None},
            {"name": "ICICI Technology Park", "type": "office", "city": "Hyderabad", "state": "Telangana", "country": "India", "lat": 17.4326, "lng": 78.3872, "climate_risk": None},
            {"name": "ICICI Regional Office Kolkata", "type": "office", "city": "Kolkata", "state": "West Bengal", "country": "India", "lat": 22.5726, "lng": 88.3639, "climate_risk": "flood_prone"},
            {"name": "ICICI Regional Office Chennai", "type": "office", "city": "Chennai", "state": "Tamil Nadu", "country": "India", "lat": 13.0827, "lng": 80.2707, "climate_risk": "water_stress"},
        ],
        "suppliers": [
            {"name": "Tata Consultancy Services", "commodity": "IT services", "tier": 1},
            {"name": "Infosys", "commodity": "technology", "tier": 1},
            {"name": "Amazon Web Services", "commodity": "cloud infrastructure", "tier": 1},
            {"name": "Wipro", "commodity": "operations outsourcing", "tier": 1},
        ],
    },
    {
        "domain": "yesbank.in",
        "name": "YES Bank Ltd",
        "industry": "Financials",
        "sasb_category": "Commercial Banks",
        "sustainability_query": '"YES Bank" ESG sustainability governance green bonds 2026',
        "general_query": '"YES Bank" corporate NPA restructuring responsibility 2026',
        "slug": "yes-bank",
        "competitors": [
            {"name": "RBL Bank", "domain": "rblbank.com", "relationship": "direct", "sub_sector": "Private Banks"},
            {"name": "Bandhan Bank", "domain": "bandhanbank.com", "relationship": "direct", "sub_sector": "Private Banks"},
            {"name": "Federal Bank", "domain": "federalbank.co.in", "relationship": "direct", "sub_sector": "Private Banks"},
            {"name": "IndusInd Bank", "domain": "indusind.com", "relationship": "direct", "sub_sector": "Private Banks"},
        ],
        "facilities": [
            {"name": "YES Bank HQ", "type": "headquarters", "city": "Mumbai", "state": "Maharashtra", "country": "India", "lat": 19.0176, "lng": 72.8562, "climate_risk": None},
            {"name": "YES Bank Lower Parel Office", "type": "office", "city": "Mumbai", "state": "Maharashtra", "country": "India", "lat": 18.9928, "lng": 72.8311, "climate_risk": None},
            {"name": "YES Bank Pune Operations", "type": "office", "city": "Pune", "state": "Maharashtra", "country": "India", "lat": 18.5204, "lng": 73.8567, "climate_risk": None},
        ],
        "suppliers": [
            {"name": "Tata Consultancy Services", "commodity": "core banking", "tier": 1},
            {"name": "Infosys", "commodity": "digital banking", "tier": 1},
            {"name": "Oracle", "commodity": "database systems", "tier": 2},
        ],
    },
    {
        "domain": "idfcfirstbank.com",
        "name": "IDFC First Bank Ltd",
        "industry": "Financials",
        "sasb_category": "Commercial Banks",
        "sustainability_query": '"IDFC First Bank" ESG sustainability financial inclusion 2026',
        "general_query": '"IDFC First Bank" corporate responsibility microfinance 2026',
        "slug": "idfc-first-bank",
        "competitors": [
            {"name": "AU Small Finance Bank", "domain": "aubank.in", "relationship": "direct", "sub_sector": "Small Finance Banks"},
            {"name": "Kotak Mahindra Bank", "domain": "kotak.com", "relationship": "direct", "sub_sector": "Private Banks"},
            {"name": "IndusInd Bank", "domain": "indusind.com", "relationship": "direct", "sub_sector": "Private Banks"},
            {"name": "Bandhan Bank", "domain": "bandhanbank.com", "relationship": "direct", "sub_sector": "Private Banks"},
        ],
        "facilities": [
            {"name": "IDFC First Bank HQ", "type": "headquarters", "city": "Mumbai", "state": "Maharashtra", "country": "India", "lat": 19.076, "lng": 72.8777, "climate_risk": None},
            {"name": "IDFC First Chennai Operations", "type": "office", "city": "Chennai", "state": "Tamil Nadu", "country": "India", "lat": 13.0827, "lng": 80.2707, "climate_risk": "water_stress"},
            {"name": "IDFC First Gurugram Tech Center", "type": "office", "city": "Gurugram", "state": "Haryana", "country": "India", "lat": 28.4595, "lng": 77.0266, "climate_risk": None},
        ],
        "suppliers": [
            {"name": "Tata Consultancy Services", "commodity": "IT services", "tier": 1},
            {"name": "Wipro", "commodity": "operations", "tier": 1},
            {"name": "Amazon Web Services", "commodity": "cloud infrastructure", "tier": 2},
        ],
    },
    {
        "domain": "waaree.com",
        "name": "Waaree Energies Ltd",
        "industry": "Renewable Resources & Alternative Energy",
        "sasb_category": "Solar Technology & Project Developers",
        "sustainability_query": '"Waaree" solar ESG sustainability supply chain 2026',
        "general_query": '"Waaree Energies" renewable energy module manufacturing 2026',
        "slug": "waaree-energies",
        "competitors": [
            {"name": "Tata Power Solar", "domain": "tatapowersolar.com", "relationship": "direct", "sub_sector": "Solar Manufacturing"},
            {"name": "Adani Solar", "domain": "adanisolar.com", "relationship": "direct", "sub_sector": "Solar Manufacturing"},
            {"name": "Vikram Solar", "domain": "vikramsolar.com", "relationship": "direct", "sub_sector": "Solar Manufacturing"},
            {"name": "Renewsys", "domain": "renewsys.com", "relationship": "direct", "sub_sector": "Solar Manufacturing"},
        ],
        "facilities": [
            {"name": "Waaree HQ Mumbai", "type": "headquarters", "city": "Mumbai", "state": "Maharashtra", "country": "India", "lat": 19.076, "lng": 72.8777, "climate_risk": None},
            {"name": "Waaree Surat Manufacturing", "type": "manufacturing", "city": "Surat", "state": "Gujarat", "country": "India", "lat": 21.1702, "lng": 72.8311, "climate_risk": "industrial_pollution"},
            {"name": "Waaree Tumb Factory", "type": "manufacturing", "city": "Tumb", "state": "Gujarat", "country": "India", "lat": 20.5, "lng": 72.7, "climate_risk": "coastal_flood"},
            {"name": "Waaree Chikhli Factory", "type": "manufacturing", "city": "Chikhli", "state": "Gujarat", "country": "India", "lat": 20.7581, "lng": 73.0614, "climate_risk": None},
        ],
        "suppliers": [
            {"name": "Tongwei Co", "commodity": "polysilicon", "tier": 1},
            {"name": "Daqo New Energy", "commodity": "polysilicon", "tier": 1},
            {"name": "Saint-Gobain", "commodity": "solar glass", "tier": 1},
            {"name": "Hindalco Industries", "commodity": "aluminum frames", "tier": 2},
            {"name": "Heraeus", "commodity": "silver paste", "tier": 2},
        ],
    },
    {
        "domain": "singularityamc.com",
        "name": "Singularity AMC Pvt Ltd",
        "industry": "Financials",
        "sasb_category": "Asset Management & Custody Activities",
        "sustainability_query": '"Singularity AMC" ESG responsible investing sustainability 2026',
        "general_query": '"Singularity" asset management fund performance 2026',
        "slug": "singularity-amc",
        "competitors": [
            {"name": "Quant AMC", "domain": "quantamc.com", "relationship": "direct", "sub_sector": "Asset Management"},
            {"name": "PPFAS Mutual Fund", "domain": "amc.ppfas.com", "relationship": "direct", "sub_sector": "Asset Management"},
            {"name": "Motilal Oswal AMC", "domain": "motilaloswalmf.com", "relationship": "direct", "sub_sector": "Asset Management"},
        ],
        "facilities": [
            {"name": "Singularity AMC HQ", "type": "headquarters", "city": "Mumbai", "state": "Maharashtra", "country": "India", "lat": 19.0658, "lng": 72.8686, "climate_risk": None},
        ],
        "suppliers": [
            {"name": "Bloomberg LP", "commodity": "financial data", "tier": 1},
            {"name": "National Stock Exchange", "commodity": "exchange services", "tier": 1},
            {"name": "CRISIL", "commodity": "credit ratings", "tier": 1},
            {"name": "Kotak Securities", "commodity": "custody services", "tier": 1},
        ],
    },
    {
        "domain": "adanipower.com",
        "name": "Adani Power Ltd",
        "industry": "Infrastructure",
        "sasb_category": "Electric Utilities & Power Generators",
        "sustainability_query": '"Adani Power" ESG emissions sustainability environment 2026',
        "general_query": '"Adani Power" coal thermal power corporate responsibility 2026',
        "slug": "adani-power",
        "competitors": [
            {"name": "NTPC Limited", "domain": "ntpc.co.in", "relationship": "direct", "sub_sector": "Thermal Power"},
            {"name": "Tata Power", "domain": "tatapower.com", "relationship": "direct", "sub_sector": "Power Generation"},
            {"name": "JSW Energy", "domain": "jsw.in", "relationship": "direct", "sub_sector": "Power Generation"},
            {"name": "Torrent Power", "domain": "torrentpower.com", "relationship": "direct", "sub_sector": "Power Generation"},
        ],
        "facilities": [
            {"name": "Adani Power HQ Ahmedabad", "type": "headquarters", "city": "Ahmedabad", "state": "Gujarat", "country": "India", "lat": 23.0225, "lng": 72.5714, "climate_risk": None},
            {"name": "Mundra Thermal Power Plant", "type": "manufacturing", "city": "Mundra", "state": "Gujarat", "country": "India", "lat": 22.8333, "lng": 69.7167, "climate_risk": "heat_stress"},
            {"name": "Tiroda Thermal Power Plant", "type": "manufacturing", "city": "Gondia", "state": "Maharashtra", "country": "India", "lat": 21.4559, "lng": 80.1962, "climate_risk": "heat_stress"},
            {"name": "Kawai Thermal Power Plant", "type": "manufacturing", "city": "Baran", "state": "Rajasthan", "country": "India", "lat": 25.1, "lng": 76.5167, "climate_risk": "drought_prone"},
            {"name": "Udupi Thermal Power Plant", "type": "manufacturing", "city": "Udupi", "state": "Karnataka", "country": "India", "lat": 13.3409, "lng": 74.7421, "climate_risk": "coastal_flood"},
        ],
        "suppliers": [
            {"name": "Coal India Limited", "commodity": "coal", "tier": 1},
            {"name": "BHEL", "commodity": "power equipment", "tier": 1},
            {"name": "Siemens Energy", "commodity": "turbines", "tier": 1},
            {"name": "Indian Railways", "commodity": "coal transport", "tier": 2},
        ],
    },
    {
        "domain": "jsw.in",
        "name": "JSW Energy Ltd",
        "industry": "Infrastructure",
        "sasb_category": "Electric Utilities & Power Generators",
        "sustainability_query": '"JSW Energy" ESG sustainability renewable transition 2026',
        "general_query": '"JSW Energy" emissions clean energy corporate responsibility 2026',
        "slug": "jsw-energy",
        "competitors": [
            {"name": "NTPC Limited", "domain": "ntpc.co.in", "relationship": "direct", "sub_sector": "Power Generation"},
            {"name": "Adani Power", "domain": "adanipower.com", "relationship": "direct", "sub_sector": "Thermal Power"},
            {"name": "Tata Power", "domain": "tatapower.com", "relationship": "direct", "sub_sector": "Power Generation"},
            {"name": "Torrent Power", "domain": "torrentpower.com", "relationship": "direct", "sub_sector": "Power Generation"},
        ],
        "facilities": [
            {"name": "JSW Energy HQ Mumbai", "type": "headquarters", "city": "Mumbai", "state": "Maharashtra", "country": "India", "lat": 19.076, "lng": 72.8777, "climate_risk": None},
            {"name": "Vijayanagar Power Plant", "type": "manufacturing", "city": "Vijayanagar", "state": "Karnataka", "country": "India", "lat": 15.4289, "lng": 76.6172, "climate_risk": None},
            {"name": "Ratnagiri Power Plant", "type": "manufacturing", "city": "Ratnagiri", "state": "Maharashtra", "country": "India", "lat": 16.9944, "lng": 73.3, "climate_risk": "coastal_flood"},
            {"name": "Barmer Power Plant", "type": "manufacturing", "city": "Barmer", "state": "Rajasthan", "country": "India", "lat": 25.7522, "lng": 71.3967, "climate_risk": "heat_stress"},
            {"name": "Salboni Power Plant", "type": "manufacturing", "city": "Salboni", "state": "West Bengal", "country": "India", "lat": 22.35, "lng": 87.05, "climate_risk": None},
        ],
        "suppliers": [
            {"name": "Coal India Limited", "commodity": "coal", "tier": 1},
            {"name": "BHEL", "commodity": "turbines", "tier": 1},
            {"name": "Siemens Energy", "commodity": "power equipment", "tier": 1},
            {"name": "Larsen & Toubro", "commodity": "construction", "tier": 2},
        ],
    },
]


async def seed_one_company(data: dict, db) -> dict:
    """Seed a single beta company with all related data."""
    domain = data["domain"]

    # Check if tenant already exists
    existing = await db.execute(select(Tenant).where(Tenant.domain == domain))
    if existing.scalars().first():
        logger.info("tenant_already_exists", domain=domain)
        return {"domain": domain, "status": "already_exists"}

    # 1. Create Tenant
    tenant = Tenant(
        name=data["name"],
        domain=domain,
        industry=data["industry"],
        sasb_category=data["sasb_category"],
        sustainability_query=data["sustainability_query"],
        general_query=data["general_query"],
    )
    db.add(tenant)
    await db.flush()
    tenant_id = tenant.id

    # 2. Create User + Membership
    user = User(
        email=f"beta@{domain}",
        domain=domain,
        name=f"Beta User ({data['name']})",
        designation="Head of Sustainability",
    )
    db.add(user)
    await db.flush()

    membership = TenantMembership(
        tenant_id=tenant_id,
        user_id=user.id,
        role="sustainability_manager",
        designation="Head of Sustainability",
        permissions=["read", "write", "analyze", "export", "manage_team"],
    )
    db.add(membership)

    # 3. Create Company
    company = Company(
        tenant_id=tenant_id,
        name=data["name"],
        slug=data["slug"],
        domain=domain,
        industry=data["industry"],
        sasb_category=data["sasb_category"],
        sustainability_query=data["sustainability_query"],
        general_query=data["general_query"],
        competitors=data["competitors"],
    )
    db.add(company)
    await db.flush()
    company_id = company.id

    # 4. Create Facilities
    for fac in data["facilities"]:
        facility = Facility(
            tenant_id=tenant_id,
            company_id=company_id,
            name=fac["name"],
            facility_type=fac["type"],
            city=fac["city"],
            state=fac.get("state"),
            country=fac.get("country", "India"),
            latitude=fac.get("lat"),
            longitude=fac.get("lng"),
            climate_risk_zone=fac.get("climate_risk"),
        )
        db.add(facility)

    # 5. Create Suppliers
    for sup in data["suppliers"]:
        supplier = Supplier(
            tenant_id=tenant_id,
            company_id=company_id,
            supplier_name=sup["name"],
            commodity=sup.get("commodity"),
            tier=sup.get("tier", 1),
        )
        db.add(supplier)

    await db.flush()
    await db.commit()

    # 6. Provision Jena knowledge graph
    try:
        from backend.ontology.tenant_provisioner import provision_tenant_graph
        from backend.ontology.geographic_intelligence import seed_facilities_to_jena
        from backend.ontology.supply_chain_graph import seed_supply_chain_to_jena

        ok = await provision_tenant_graph(
            tenant_id=tenant_id,
            tenant_name=data["name"],
            industry=data["industry"],
            sasb_category=data["sasb_category"],
            domain=domain,
            company_id=company_id,
        )

        # Need fresh session for Jena seeding (post-commit)
        async with async_session_factory() as db2:
            await seed_facilities_to_jena(company_id, tenant_id, db2)
            await seed_supply_chain_to_jena(company_id, tenant_id, db2)

        from backend.ontology.jena_client import jena_client
        triple_count = await jena_client.count_triples(tenant_id)

        logger.info(
            "beta_company_seeded",
            domain=domain,
            company=data["name"],
            tenant_id=tenant_id,
            company_id=company_id,
            facilities=len(data["facilities"]),
            suppliers=len(data["suppliers"]),
            competitors=len(data["competitors"]),
            jena_triples=triple_count,
            jena_ok=ok,
        )
    except Exception as e:
        logger.error("jena_provisioning_failed", domain=domain, error=str(e))

    return {
        "domain": domain,
        "status": "seeded",
        "tenant_id": tenant_id,
        "company_id": company_id,
        "facilities": len(data["facilities"]),
        "suppliers": len(data["suppliers"]),
    }


async def trigger_news_ingestion(results: list[dict]):
    """Trigger news ingestion for all successfully seeded tenants."""
    for r in results:
        if r["status"] != "seeded":
            continue
        try:
            from backend.services.news_service import curate_domain_news
            from backend.core.database import async_session_factory
            from backend.models.tenant import Tenant
            from backend.models.company import Company

            async with async_session_factory() as db:
                tenant = await db.execute(select(Tenant).where(Tenant.id == r["tenant_id"]))
                t = tenant.scalars().first()
                comp = await db.execute(select(Company).where(Company.id == r["company_id"]))
                c = comp.scalars().first()

                if t and c:
                    from backend.tasks.news_tasks import ingest_news_for_tenant
                    # Try direct call first, fall back to Celery delay
                    try:
                        ingest_news_for_tenant.delay(
                            t.id, c.name,
                            t.sustainability_query or f'"{c.name}" ESG',
                            t.general_query or f'"{c.name}" corporate responsibility',
                        )
                        logger.info("news_ingestion_triggered", domain=r["domain"], method="celery")
                    except Exception:
                        logger.info("news_ingestion_skipped_no_celery", domain=r["domain"])

        except Exception as e:
            logger.warning("news_trigger_failed", domain=r["domain"], error=str(e))


async def main():
    """Seed all 7 beta companies."""
    print(f"\n{'='*60}")
    print(f"  SNOWKAP ESG — Beta Company Seeding")
    print(f"  Seeding {len(BETA_COMPANIES)} companies...")
    print(f"{'='*60}\n")

    results = []

    for i, data in enumerate(BETA_COMPANIES, 1):
        print(f"[{i}/{len(BETA_COMPANIES)}] Seeding {data['name']} ({data['domain']})...")
        async with async_session_factory() as db:
            result = await seed_one_company(data, db)
            results.append(result)
            print(f"  -> {result['status']}")

    print(f"\n{'='*60}")
    print(f"  Seeding Complete!")
    print(f"{'='*60}")
    for r in results:
        status = "OK" if r["status"] == "seeded" else r["status"]
        print(f"  {r['domain']:30s} {status}")

    # Trigger news ingestion
    print(f"\nTriggering news ingestion...")
    await trigger_news_ingestion(results)

    print(f"\nDone! Run the analysis pipeline next:")
    print(f"  python -m backend.scripts.analyze_beta")


if __name__ == "__main__":
    asyncio.run(main())
