"""Tenant ontology provisioner — auto-provision company in Jena on first login.

Per MASTER_BUILD_PLAN Phase 2C + Phase 3:
- Auto-provision company node in Jena knowledge graph on first login
- Create tenant named graph with base ontology
- Seed company + industry + framework links
- Seed facilities and supply chain data
"""

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.company import Company, Facility, Supplier
from backend.models.tenant import Tenant
from backend.ontology.jena_client import jena_client
from backend.ontology.geographic_intelligence import seed_facilities_to_jena
from backend.ontology.supply_chain_graph import seed_supply_chain_to_jena

logger = structlog.get_logger()

SNOWKAP_NS = "http://snowkap.com/ontology/esg#"

# SASB industry → material ESG issues mapping
INDUSTRY_MATERIAL_ISSUES = {
    "Transportation": ["emissions", "fuel_management", "driver_safety", "fleet_efficiency"],
    "Technology & Communications": ["data_privacy", "energy_management", "e_waste", "workforce_diversity"],
    "Financials": ["data_privacy", "business_ethics", "systemic_risk", "financial_inclusion"],
    "Health Care": ["drug_safety", "access_to_care", "data_privacy", "waste_management"],
    "Consumer Goods": ["packaging", "supply_chain_labor", "product_safety", "water_management"],
    "Extractives & Minerals Processing": ["emissions", "water_management", "biodiversity", "community_relations"],
    "Infrastructure": ["energy_management", "water_management", "workforce_safety", "climate_adaptation"],
    "Resource Transformation": ["emissions", "energy_management", "waste", "worker_safety"],
    "Food & Beverage": ["water_management", "food_safety", "supply_chain_ethics", "packaging"],
    "Services": ["data_privacy", "workforce_management", "energy_management", "business_ethics"],
    "Renewable Resources & Alternative Energy": ["lifecycle_impacts", "ecological_impacts", "workforce_safety"],
}

# Framework → relevant pillars
FRAMEWORK_PILLARS = {
    "BRSR": ["E", "S", "G"],
    "GRI": ["E", "S", "G"],
    "TCFD": ["E", "G"],
    "CDP": ["E"],
    "SASB": ["E", "S", "G"],
    "ESRS": ["E", "S", "G"],
    "CSRD": ["E", "S", "G"],
    "IFRS_S1": ["E", "S", "G"],
    "IFRS_S2": ["E"],
}


async def provision_tenant_graph(
    tenant_id: str,
    tenant_name: str,
    industry: str | None,
    sasb_category: str | None,
    domain: str,
) -> bool:
    """Create and populate the tenant's named graph in Jena.

    Steps:
    1. Upload base ontology to tenant graph
    2. Create company node
    3. Link to industry and material issues
    4. Link to relevant ESG frameworks
    """
    graph_uri = jena_client._tenant_graph(tenant_id)

    # 1. Upload base ontology
    try:
        import importlib.resources as pkg_resources
        from pathlib import Path

        ttl_path = Path(__file__).parent / "sustainability.ttl"
        base_ttl = ttl_path.read_text(encoding="utf-8")
        await jena_client.upload_ttl(base_ttl, graph_uri)
        logger.info("base_ontology_uploaded", tenant_id=tenant_id)
    except Exception as e:
        logger.error("base_ontology_upload_failed", tenant_id=tenant_id, error=str(e))
        return False

    # 2. Create company + tenant triples
    company_uri = f"<{SNOWKAP_NS}company_{tenant_id}>"
    triples: list[tuple[str, str, str]] = [
        (company_uri, "a", f"<{SNOWKAP_NS}Company>"),
        (company_uri, "rdfs:label", f'"{tenant_name}"'),
        (company_uri, f"<{SNOWKAP_NS}domain>", f'"{domain}"'),
    ]

    # 3. Link to industry
    if industry:
        industry_slug = industry.lower().replace(" ", "_").replace("&", "and")
        industry_uri = f"<{SNOWKAP_NS}industry_{industry_slug}>"
        triples.append((industry_uri, "a", f"<{SNOWKAP_NS}Industry>"))
        triples.append((industry_uri, "rdfs:label", f'"{industry}"'))
        triples.append((company_uri, f"<{SNOWKAP_NS}belongsToIndustry>", industry_uri))

        # Link to material issues
        material_issues = INDUSTRY_MATERIAL_ISSUES.get(industry, [])
        for issue in material_issues:
            issue_uri = f"<{SNOWKAP_NS}issue_{issue}>"
            triples.append((issue_uri, "a", f"<{SNOWKAP_NS}MaterialIssue>"))
            triples.append((issue_uri, "rdfs:label", f'"{issue.replace("_", " ")}"'))
            triples.append((company_uri, f"<{SNOWKAP_NS}hasMaterialIssue>", issue_uri))

    # 4. Link to SASB category
    if sasb_category:
        triples.append((company_uri, f"<{SNOWKAP_NS}sasbCategory>", f'"{sasb_category}"'))

    # 5. Link to default ESG frameworks (BRSR mandatory for India, + common ones)
    default_frameworks = ["BRSR", "GRI", "SASB"]
    for fw in default_frameworks:
        fw_uri = f"<{SNOWKAP_NS}{fw}>"
        triples.append((company_uri, f"<{SNOWKAP_NS}reportsUnder>", fw_uri))

    success = await jena_client.insert_triples(triples, tenant_id)

    if success:
        logger.info(
            "tenant_graph_provisioned",
            tenant_id=tenant_id,
            company=tenant_name,
            industry=industry,
            triples=len(triples),
        )
    return success


async def provision_full_tenant_ontology(
    tenant_id: str,
    db: AsyncSession,
) -> dict:
    """Full ontology provisioning for an existing tenant — seeds all data.

    Called after initial company/facility/supplier data is available.
    Steps:
    1. Provision base tenant graph (if not already done)
    2. Seed all companies for the tenant
    3. Seed facilities (geographic intelligence)
    4. Seed supply chain data
    """
    # Get tenant info
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        return {"error": "Tenant not found"}

    stats = {"tenant_id": tenant_id, "companies": 0, "facilities": 0, "suppliers": 0}

    # Check if graph already exists
    exists = await jena_client.graph_exists(tenant_id)
    if not exists:
        await provision_tenant_graph(
            tenant_id=tenant_id,
            tenant_name=tenant.name,
            industry=tenant.industry,
            sasb_category=tenant.sasb_category,
            domain=tenant.domain,
        )

    # Seed all companies
    companies_result = await db.execute(
        select(Company).where(Company.tenant_id == tenant_id)
    )
    companies = companies_result.scalars().all()

    for company in companies:
        triples: list[tuple[str, str, str]] = []
        comp_uri = f"<{SNOWKAP_NS}company_{company.id}>"

        triples.append((comp_uri, "a", f"<{SNOWKAP_NS}Company>"))
        triples.append((comp_uri, "rdfs:label", f'"{company.name}"'))

        if company.industry:
            ind_slug = company.industry.lower().replace(" ", "_").replace("&", "and")
            ind_uri = f"<{SNOWKAP_NS}industry_{ind_slug}>"
            triples.append((ind_uri, "a", f"<{SNOWKAP_NS}Industry>"))
            triples.append((ind_uri, "rdfs:label", f'"{company.industry}"'))
            triples.append((comp_uri, f"<{SNOWKAP_NS}belongsToIndustry>", ind_uri))

        if company.domain:
            triples.append((comp_uri, f"<{SNOWKAP_NS}domain>", f'"{company.domain}"'))

        await jena_client.insert_triples(triples, tenant_id)
        stats["companies"] += 1

        # Seed facilities
        await seed_facilities_to_jena(company.id, tenant_id, db)
        fac_result = await db.execute(
            select(Facility).where(Facility.company_id == company.id)
        )
        stats["facilities"] += len(fac_result.scalars().all())

        # Seed supply chain
        await seed_supply_chain_to_jena(company.id, tenant_id, db)
        sup_result = await db.execute(
            select(Supplier).where(Supplier.company_id == company.id)
        )
        stats["suppliers"] += len(sup_result.scalars().all())

    triple_count = await jena_client.count_triples(tenant_id)
    stats["total_triples"] = triple_count

    logger.info("full_ontology_provisioned", **stats)
    return stats
