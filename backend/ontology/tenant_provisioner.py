"""Tenant ontology provisioner — auto-provision company in Jena on first login.

Per MASTER_BUILD_PLAN Phase 2C + Phase 3:
- Auto-provision company node in Jena knowledge graph on first login
- Create tenant named graph with base ontology
- Seed company + industry + framework links
- Seed facilities and supply chain data

Stage 3.4: Expand from 3 to all 9 frameworks. Create MATERIAL_ISSUE_TO_FRAMEWORK
mapping. Auto-generate OWL rules during provisioning.
"""

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.company import Company, Facility, Supplier
from backend.models.tenant import Tenant
from backend.ontology.jena_client import jena_client
from backend.ontology.geographic_intelligence import seed_facilities_to_jena
from backend.ontology.rule_compiler import compile_and_deploy_rule
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

# Stage 3.4: All 9 frameworks (was 3)
ALL_FRAMEWORKS = ["BRSR", "GRI", "SASB", "TCFD", "CDP", "ESRS", "IFRS_S1", "IFRS_S2", "CSRD"]

# Stage 3.4: Material issue → framework indicator mappings
MATERIAL_ISSUE_TO_FRAMEWORK: dict[str, list[tuple[str, str]]] = {
    # (framework, indicator)
    "emissions": [
        ("BRSR", "P6"), ("GRI", "305"), ("TCFD", "Metrics"),
        ("CDP", "Climate"), ("ESRS", "E1"), ("IFRS_S2", "Scope1-3"),
    ],
    "water_management": [
        ("BRSR", "P6"), ("GRI", "303"), ("CDP", "Water"), ("ESRS", "E3"),
    ],
    "water_scarcity": [
        ("BRSR", "P6"), ("GRI", "303"), ("CDP", "Water"), ("ESRS", "E3"),
    ],
    "data_privacy": [
        ("BRSR", "P9"), ("GRI", "418"), ("ESRS", "S4"),
    ],
    "business_ethics": [
        ("BRSR", "P1"), ("GRI", "205"), ("ESRS", "G1"),
    ],
    "anti_corruption": [
        ("BRSR", "P1"), ("GRI", "205"), ("ESRS", "G1"),
    ],
    "energy_management": [
        ("BRSR", "P6"), ("GRI", "302"), ("TCFD", "Metrics"),
        ("CDP", "Climate"), ("ESRS", "E1"), ("IFRS_S2", "Energy"),
    ],
    "waste": [
        ("BRSR", "P6"), ("GRI", "306"), ("ESRS", "E5"),
    ],
    "waste_management": [
        ("BRSR", "P6"), ("GRI", "306"), ("ESRS", "E5"),
    ],
    "biodiversity": [
        ("BRSR", "P6"), ("GRI", "304"), ("ESRS", "E4"), ("CDP", "Forests"),
    ],
    "worker_safety": [
        ("BRSR", "P3"), ("GRI", "403"), ("ESRS", "S2"),
    ],
    "workforce_safety": [
        ("BRSR", "P3"), ("GRI", "403"), ("ESRS", "S2"),
    ],
    "workforce_diversity": [
        ("BRSR", "P5"), ("GRI", "405"), ("ESRS", "S1"),
    ],
    "workforce_management": [
        ("BRSR", "P3"), ("GRI", "401"), ("ESRS", "S1"),
    ],
    "supply_chain_labor": [
        ("BRSR", "P5"), ("GRI", "414"), ("ESRS", "S2"),
    ],
    "supply_chain_ethics": [
        ("BRSR", "P5"), ("GRI", "414"), ("ESRS", "S2"),
    ],
    "community_relations": [
        ("BRSR", "P8"), ("GRI", "413"), ("ESRS", "S3"),
    ],
    "product_safety": [
        ("BRSR", "P9"), ("GRI", "416"), ("ESRS", "S4"),
    ],
    "food_safety": [
        ("BRSR", "P9"), ("GRI", "416"), ("ESRS", "S4"),
    ],
    "drug_safety": [
        ("BRSR", "P9"), ("GRI", "416"), ("ESRS", "S4"),
    ],
    "packaging": [
        ("BRSR", "P6"), ("GRI", "301"), ("ESRS", "E5"),
    ],
    "fuel_management": [
        ("BRSR", "P6"), ("GRI", "302"), ("TCFD", "Metrics"), ("IFRS_S2", "Energy"),
    ],
    "driver_safety": [
        ("BRSR", "P3"), ("GRI", "403"), ("ESRS", "S2"),
    ],
    "fleet_efficiency": [
        ("BRSR", "P6"), ("GRI", "305"), ("TCFD", "Metrics"),
    ],
    "e_waste": [
        ("BRSR", "P6"), ("GRI", "306"), ("ESRS", "E5"),
    ],
    "systemic_risk": [
        ("BRSR", "P1"), ("TCFD", "Risk"), ("IFRS_S1", "Risks"),
    ],
    "financial_inclusion": [
        ("BRSR", "P8"), ("GRI", "203"), ("ESRS", "S3"),
    ],
    "access_to_care": [
        ("BRSR", "P8"), ("GRI", "203"), ("ESRS", "S4"),
    ],
    "climate_adaptation": [
        ("BRSR", "P6"), ("TCFD", "Strategy"), ("ESRS", "E1"), ("IFRS_S2", "Resilience"),
    ],
    "lifecycle_impacts": [
        ("BRSR", "P6"), ("GRI", "301"), ("ESRS", "E5"),
    ],
    "ecological_impacts": [
        ("BRSR", "P6"), ("GRI", "304"), ("ESRS", "E4"),
    ],
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
    4. Link to ALL 9 ESG frameworks (Stage 3.4)
    5. Auto-generate framework indicator rules per material issue
    """
    graph_uri = jena_client._tenant_graph(tenant_id)

    # 1. Upload base ontology
    try:
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

    # 3. Link to industry + material issues
    material_issues: list[str] = []
    if industry:
        industry_slug = industry.lower().replace(" ", "_").replace("&", "and")
        industry_uri = f"<{SNOWKAP_NS}industry_{industry_slug}>"
        triples.append((industry_uri, "a", f"<{SNOWKAP_NS}Industry>"))
        triples.append((industry_uri, "rdfs:label", f'"{industry}"'))
        triples.append((company_uri, f"<{SNOWKAP_NS}belongsToIndustry>", industry_uri))

        material_issues = INDUSTRY_MATERIAL_ISSUES.get(industry, [])
        for issue in material_issues:
            issue_uri = f"<{SNOWKAP_NS}issue_{issue}>"
            triples.append((issue_uri, "a", f"<{SNOWKAP_NS}MaterialIssue>"))
            triples.append((issue_uri, "rdfs:label", f'"{issue.replace("_", " ")}"'))
            triples.append((company_uri, f"<{SNOWKAP_NS}hasMaterialIssue>", issue_uri))

    # 4. Link to SASB category
    if sasb_category:
        triples.append((company_uri, f"<{SNOWKAP_NS}sasbCategory>", f'"{sasb_category}"'))

    # 5. Stage 3.4: Link to ALL 9 frameworks (was 3)
    for fw in ALL_FRAMEWORKS:
        fw_uri = f"<{SNOWKAP_NS}{fw}>"
        triples.append((fw_uri, "a", f"<{SNOWKAP_NS}Framework>"))
        triples.append((fw_uri, "rdfs:label", f'"{fw}"'))
        pillars = FRAMEWORK_PILLARS.get(fw, [])
        for pillar in pillars:
            triples.append((fw_uri, f"<{SNOWKAP_NS}esgPillar>", f'"{pillar}"'))
        triples.append((company_uri, f"<{SNOWKAP_NS}reportsUnder>", fw_uri))

    # 6. Stage 3.4: Auto-generate framework indicator rules per material issue
    for issue in material_issues:
        mappings = MATERIAL_ISSUE_TO_FRAMEWORK.get(issue, [])
        for framework, indicator in mappings:
            issue_uri = f"<{SNOWKAP_NS}issue_{issue}>"
            fw_uri = f"<{SNOWKAP_NS}{framework}>"
            triples.append((issue_uri, f"<{SNOWKAP_NS}reportsUnder>", fw_uri))
            triples.append((issue_uri, f"<{SNOWKAP_NS}frameworkIndicator>", f'"{framework}:{indicator}"'))

    success = await jena_client.insert_triples(triples, tenant_id)

    if success:
        logger.info(
            "tenant_graph_provisioned",
            tenant_id=tenant_id,
            company=tenant_name,
            industry=industry,
            frameworks=len(ALL_FRAMEWORKS),
            triples=len(triples),
        )
    return success


async def provision_full_tenant_ontology(
    tenant_id: str,
    db: AsyncSession,
) -> dict:
    """Full ontology provisioning for an existing tenant — seeds all data.

    Called after initial company/facility/supplier data is available.
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

        # Link company to all 9 frameworks
        for fw in ALL_FRAMEWORKS:
            fw_uri = f"<{SNOWKAP_NS}{fw}>"
            triples.append((comp_uri, f"<{SNOWKAP_NS}reportsUnder>", fw_uri))

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
