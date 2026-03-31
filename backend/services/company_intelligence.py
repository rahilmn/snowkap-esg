"""Auto-provision ANY company from domain name using LLM intelligence.

Phase 3: When a new user signs up with domain=xyz.com, this service:
1. Uses gpt-4o-mini to discover company info (industry, facilities, suppliers)
2. Creates Company + Facility + Supplier records in DB
3. Provisions Jena knowledge graph via tenant_provisioner
4. Triggers first news ingestion

Cost: ~$0.002 per company (gpt-4o-mini).
"""

import json

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import llm
from backend.models.company import Company, Facility, Supplier
from backend.models.base import generate_uuid
from backend.models.tenant import Tenant

logger = structlog.get_logger()


async def auto_provision_company(
    domain: str,
    tenant_id: str,
    db: AsyncSession,
) -> dict:
    """Auto-discover company info from domain and provision everything.

    Called during signup after tenant is created.
    Returns dict with provisioning results.
    """
    # Check if company already exists for this tenant
    existing = await db.execute(
        select(Company).where(Company.tenant_id == tenant_id)
    )
    if existing.scalars().first():
        logger.info("company_already_provisioned", tenant_id=tenant_id)
        return {"status": "already_provisioned"}

    if not llm.is_configured():
        logger.warning("llm_not_configured_for_provisioning")
        return {"status": "llm_not_configured"}

    # Step 1: Use LLM to discover company info from domain
    prompt = f"""Given the corporate domain "{domain}", provide information about the company.
Return ONLY a JSON object with this exact structure (no markdown, no explanation):
{{
  "company_name": "Full legal company name",
  "industry": "SASB Industry Category (e.g., Consumer Goods, Technology & Communications, Financials, Health Care, Extractives & Minerals Processing, Infrastructure, Resource Transformation, Food & Beverage, Services, Transportation, Renewable Resources & Alternative Energy)",
  "sasb_category": "Specific SASB sub-category",
  "headquarters": {{
    "city": "City name",
    "country": "Country",
    "lat": 0.0,
    "lng": 0.0
  }},
  "facilities": [
    {{
      "name": "Facility name",
      "city": "City",
      "country": "Country",
      "lat": 0.0,
      "lng": 0.0,
      "type": "manufacturing or distribution or headquarters or office or data_center",
      "climate_risk": "water_stress or coastal_flood or heat_stress or drought_prone or null"
    }}
  ],
  "suppliers": [
    {{
      "name": "Supplier name",
      "commodity": "What they supply",
      "country": "Country",
      "tier": 1
    }}
  ],
  "competitors": [
    {{"name": "Competitor company name", "domain": "competitor.com",
      "relationship": "direct or indirect",
      "sub_sector": "Specific sub-sector they compete in"}}
  ],
  "sustainability_query": "Company ESG sustainability search query for news",
  "general_query": "Company corporate responsibility search query"
}}

Rules:
- Include 3-5 major competitors (direct = same sub-sector, indirect = same industry)
- Include 3-5 major facilities (from public knowledge — annual reports, sustainability reports)
- Include 3-5 tier-1 suppliers (from public supply chain disclosures)
- For climate_risk: water_stress for cities in India/Middle East/Africa, coastal_flood for coastal factories, heat_stress for tropical regions
- Lat/lng should be approximate city center coordinates
- If you don't know, use null for climate_risk and 0.0 for lat/lng
- sustainability_query should include: company name + ESG + sustainability + key industry terms
- general_query should include: company name + corporate responsibility + environment"""

    try:
        raw_text = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            model="gpt-4o-mini",  # Cost: ~$0.002 per company
        )
        raw_text = raw_text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        data = json.loads(raw_text)
    except (json.JSONDecodeError, Exception) as e:
        logger.error("company_discovery_failed", domain=domain, error=str(e))
        return {"status": "discovery_failed", "error": str(e)}

    company_name = data.get("company_name", domain.split(".")[0].title())
    industry = data.get("industry", "Services")
    sasb_category = data.get("sasb_category")

    # Step 2: Update tenant with industry info
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if tenant:
        if not tenant.industry:
            tenant.industry = industry
        if not tenant.sasb_category:
            tenant.sasb_category = sasb_category
        if not tenant.sustainability_query:
            tenant.sustainability_query = data.get("sustainability_query", f"{company_name} ESG sustainability")
        if not tenant.general_query:
            tenant.general_query = data.get("general_query", f"{company_name} corporate responsibility")

    # Step 3: Create Company
    slug = company_name.lower().replace(" ", "-").replace(".", "")[:50]
    # Parse competitors from LLM response
    competitors_data = data.get("competitors", [])
    if isinstance(competitors_data, list):
        competitors_data = [c for c in competitors_data if isinstance(c, dict) and c.get("name")]
    else:
        competitors_data = []

    company = Company(
        tenant_id=tenant_id,
        name=company_name,
        slug=slug,
        domain=domain,
        industry=industry,
        sasb_category=sasb_category,
        status="active",
        competitors=competitors_data if competitors_data else None,
    )
    db.add(company)
    await db.flush()  # Get company.id

    stats = {"company": company_name, "facilities": 0, "suppliers": 0}

    # Step 4: Create Facilities
    hq = data.get("headquarters", {})
    if hq.get("city"):
        facility = Facility(
            tenant_id=tenant_id,
            company_id=company.id,
            name=f"{company_name} Headquarters",
            facility_type="headquarters",
            city=hq.get("city"),
            country=hq.get("country"),
            latitude=hq.get("lat"),
            longitude=hq.get("lng"),
            climate_risk_zone=None,
        )
        db.add(facility)
        stats["facilities"] += 1

    for fac_data in data.get("facilities", []):
        try:
            facility = Facility(
                tenant_id=tenant_id,
                company_id=company.id,
                name=fac_data.get("name", "Unknown Facility"),
                facility_type=fac_data.get("type", "office"),
                city=fac_data.get("city"),
                country=fac_data.get("country"),
                latitude=fac_data.get("lat") if fac_data.get("lat") else None,
                longitude=fac_data.get("lng") if fac_data.get("lng") else None,
                climate_risk_zone=fac_data.get("climate_risk"),
            )
            db.add(facility)
            stats["facilities"] += 1
        except Exception as e:
            logger.warning("facility_creation_failed", error=str(e))

    # Step 5: Create Suppliers
    for sup_data in data.get("suppliers", []):
        try:
            supplier = Supplier(
                tenant_id=tenant_id,
                company_id=company.id,
                supplier_name=sup_data.get("name", "Unknown Supplier"),
                commodity=sup_data.get("commodity"),
                tier=sup_data.get("tier", 1),
            )
            db.add(supplier)
            stats["suppliers"] += 1
        except Exception as e:
            logger.warning("supplier_creation_failed", error=str(e))

    await db.flush()

    # Step 6: Provision Jena knowledge graph (with company_id for unified URIs)
    try:
        from backend.ontology.tenant_provisioner import provision_tenant_graph
        await provision_tenant_graph(
            tenant_id=tenant_id,
            tenant_name=company_name,
            industry=industry,
            sasb_category=sasb_category,
            domain=domain,
            company_id=company.id,
        )
        stats["jena_provisioned"] = True
    except Exception as e:
        logger.error("jena_provisioning_failed", error=str(e))
        stats["jena_provisioned"] = False

    # Step 6b: Seed facilities + supply chain to Jena for multi-hop causal chains
    try:
        from backend.ontology.geographic_intelligence import seed_facilities_to_jena
        from backend.ontology.supply_chain_graph import seed_supply_chain_to_jena
        await seed_facilities_to_jena(company.id, tenant_id, db)
        await seed_supply_chain_to_jena(company.id, tenant_id, db)
        stats["facilities_seeded"] = True
        stats["suppliers_seeded"] = True
    except Exception as e:
        logger.warning("jena_facility_supplier_seeding_failed", error=str(e))
        stats["facilities_seeded"] = False
        stats["suppliers_seeded"] = False

    # Step 7: Trigger first news ingestion (async via Celery if available)
    try:
        from backend.tasks.news_tasks import ingest_news_for_tenant
        ingest_news_for_tenant.delay(
            tenant_id=tenant_id,
            company_name=company_name,
            sustainability_query=tenant.sustainability_query if tenant else f"{company_name} ESG",
            general_query=tenant.general_query if tenant else f"{company_name} corporate responsibility",
        )
        stats["news_triggered"] = True
    except Exception:
        stats["news_triggered"] = False

    logger.info("company_auto_provisioned", **stats)
    return stats
