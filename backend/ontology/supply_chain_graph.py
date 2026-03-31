"""Supply Chain Graph — building and querying supply chain knowledge.

Per MASTER_BUILD_PLAN Phase 3.4:
- Company → Tier 1 suppliers (from public data + user input)
- Industry → typical supply chain shape (auto-generated via Claude)
- Commodity dependency mapping (steel → iron ore → mining → coal)
- Scope 3 category linkage (upstream/downstream)
"""

import json
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import llm
from backend.core.config import settings
from backend.models.company import Company, Supplier
from backend.ontology.jena_client import jena_client

logger = structlog.get_logger()

SNOWKAP_NS = "http://snowkap.com/ontology/esg#"

# Standard commodity dependency chains — 25 SASB industries (Stage 2.7)
COMMODITY_CHAINS = {
    # Original 10
    "steel": ["iron_ore", "coal", "limestone", "mining"],
    "plastic": ["oil", "petrochemicals", "naphtha", "refining"],
    "electronics": ["semiconductors", "rare_earth", "copper", "lithium", "mining"],
    "textiles": ["cotton", "polyester", "dyes", "chemicals", "water"],
    "automotive": ["steel", "aluminum", "rubber", "semiconductors", "glass"],
    "food_processing": ["agriculture", "water", "packaging", "cold_chain", "transport"],
    "pharmaceuticals": ["chemicals", "packaging", "cold_chain", "water"],
    "construction": ["cement", "steel", "sand", "timber", "water"],
    "logistics": ["fuel", "vehicles", "tires", "lubricants"],
    "it_services": ["electricity", "cooling", "hardware", "fiber_optics"],
    # Stage 2.7: 15 new industries
    "renewable_energy": ["solar_panels", "silicon", "rare_earth", "copper", "lithium", "wind_turbines"],
    "fashion": ["cotton", "polyester", "leather", "dyes", "water", "chemicals", "textiles"],
    "mining": ["explosives", "diesel", "heavy_machinery", "water", "electricity"],
    "chemicals": ["oil", "natural_gas", "minerals", "water", "catalysts"],
    "oil_gas": ["drilling_equipment", "steel", "chemicals", "water", "transport"],
    "metals": ["iron_ore", "bauxite", "copper_ore", "coal", "electricity", "water"],
    "banking": ["electricity", "data_centers", "real_estate", "paper"],
    "healthcare": ["pharmaceuticals", "medical_devices", "chemicals", "cold_chain", "plastics"],
    "telecom": ["fiber_optics", "copper", "electricity", "semiconductors", "towers"],
    "agriculture": ["seeds", "fertilizers", "pesticides", "water", "diesel", "machinery"],
    "cement": ["limestone", "clay", "coal", "electricity", "gypsum", "water"],
    "shipping": ["fuel_oil", "steel", "containers", "port_services", "insurance"],
    "aviation": ["jet_fuel", "aluminum", "titanium", "electronics", "rubber"],
    "hospitality": ["food", "water", "electricity", "textiles", "cleaning_chemicals"],
    "food_beverage": ["agriculture", "sugar", "water", "packaging", "cold_chain", "flavors"],
}

# Scope 3 categories per supply chain direction
SCOPE3_UPSTREAM = {
    "category_1": "Purchased goods and services",
    "category_2": "Capital goods",
    "category_3": "Fuel- and energy-related activities",
    "category_4": "Upstream transportation and distribution",
    "category_5": "Waste generated in operations",
    "category_6": "Business travel",
    "category_7": "Employee commuting",
    "category_8": "Upstream leased assets",
}

SCOPE3_DOWNSTREAM = {
    "category_9": "Downstream transportation and distribution",
    "category_10": "Processing of sold products",
    "category_11": "Use of sold products",
    "category_12": "End-of-life treatment of sold products",
    "category_13": "Downstream leased assets",
    "category_14": "Franchises",
    "category_15": "Investments",
}


@dataclass
class SupplyChainNode:
    """A node in the supply chain graph."""
    name: str
    node_type: str  # supplier, commodity, company, industry
    tier: int = 1
    uri: str | None = None
    scope3_category: str | None = None


@dataclass
class SupplyChainEdge:
    """An edge in the supply chain graph."""
    source: str
    target: str
    relationship: str  # suppliesTo, sourcesFrom, dependsOnCommodity
    commodity: str | None = None
    tier: int = 1


async def seed_supply_chain_to_jena(
    company_id: str,
    tenant_id: str,
    db: AsyncSession,
) -> bool:
    """Seed a company's supply chain into the tenant's Jena graph.

    Creates supplier nodes, commodity nodes, and links them to the company.
    """
    result = await db.execute(
        select(Supplier, Company)
        .join(Company, Supplier.company_id == Company.id)
        .where(Supplier.company_id == company_id, Supplier.tenant_id == tenant_id)
    )
    rows = result.all()

    company_result = await db.execute(
        select(Company).where(Company.id == company_id, Company.tenant_id == tenant_id)
    )
    company = company_result.scalar_one_or_none()
    if not company:
        return False

    triples: list[tuple[str, str, str]] = []
    comp_uri = f"<{SNOWKAP_NS}company_{company.id}>"

    for supplier, _ in rows:
        sup_uri = f"<{SNOWKAP_NS}supplier_{supplier.id}>"

        triples.append((sup_uri, "a", f"<{SNOWKAP_NS}Supplier>"))
        triples.append((sup_uri, "rdfs:label", f'"{supplier.supplier_name}"'))
        triples.append((sup_uri, f"<{SNOWKAP_NS}suppliesTo>", comp_uri))
        # Reverse edge: company sources from supplier (enables BFS from company outward)
        triples.append((comp_uri, f"<{SNOWKAP_NS}sourcesFrom>", sup_uri))

        if supplier.commodity:
            commodity_uri = f"<{SNOWKAP_NS}commodity_{supplier.commodity.lower().replace(' ', '_')}>"
            triples.append((commodity_uri, "a", f"<{SNOWKAP_NS}Commodity>"))
            triples.append((commodity_uri, "rdfs:label", f'"{supplier.commodity}"'))
            triples.append((comp_uri, f"<{SNOWKAP_NS}dependsOnCommodity>", commodity_uri))
            # Reverse: commodity is depended on by company (enables BFS from commodity to company)
            triples.append((commodity_uri, f"<{SNOWKAP_NS}suppliesTo>", comp_uri))
            # Supplier provides the commodity
            triples.append((sup_uri, f"<{SNOWKAP_NS}provides>", commodity_uri))

            # Add commodity chain dependencies
            chain = COMMODITY_CHAINS.get(supplier.commodity.lower(), [])
            prev_uri = commodity_uri
            for dep in chain:
                dep_uri = f"<{SNOWKAP_NS}commodity_{dep}>"
                triples.append((dep_uri, "a", f"<{SNOWKAP_NS}Commodity>"))
                triples.append((dep_uri, "rdfs:label", f'"{dep.replace("_", " ")}"'))
                triples.append((prev_uri, f"<{SNOWKAP_NS}dependsOnCommodity>", dep_uri))
                prev_uri = dep_uri

        if supplier.scope3_category:
            triples.append((sup_uri, f"<{SNOWKAP_NS}scope3Category>", f'"{supplier.scope3_category}"'))

    if triples:
        return await jena_client.insert_triples(triples, tenant_id)
    return True


async def generate_industry_supply_chain(
    company_name: str,
    industry: str,
    tenant_id: str,
) -> list[SupplyChainNode]:
    """Auto-generate a typical supply chain for an industry via Claude.

    Per MASTER_BUILD_PLAN Phase 3.4:
    Industry → typical supply chain shape (auto-generated via Claude)
    """
    if not llm.is_configured():
        return []

    prompt = f"""For the company "{company_name}" in the "{industry}" industry, generate a typical
supply chain mapping. Return a JSON array of supply chain nodes.

For each node provide:
- "name": supplier/commodity name
- "type": "supplier" | "commodity" | "service"
- "tier": 1 (direct) to 3 (indirect)
- "scope3_category": Scope 3 category number (1-15)
- "commodity": main commodity if applicable
- "esg_risk": main ESG risk (e.g., "emissions", "water", "labor")

Focus on India-relevant supply chains. Include 10-15 key nodes.
Return JSON array only, no markdown."""

    try:
        raw_text = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
        )
        nodes_raw = json.loads(raw_text)
        nodes = [
            SupplyChainNode(
                name=n["name"],
                node_type=n.get("type", "supplier"),
                tier=n.get("tier", 1),
                scope3_category=n.get("scope3_category"),
            )
            for n in nodes_raw
        ]
        logger.info(
            "supply_chain_generated",
            company=company_name,
            industry=industry,
            nodes=len(nodes),
        )
        return nodes
    except Exception as e:
        logger.error("supply_chain_generation_failed", error=str(e))
        return []


async def query_supply_chain_exposure(
    company_id: str,
    commodity: str,
    tenant_id: str,
) -> list[dict]:
    """Query supply chain exposure to a specific commodity via Jena.

    Example: "Show all paths from 'coal' to company X through supply chain"
    """
    graph_uri = jena_client._tenant_graph(tenant_id)
    company_uri = f"{SNOWKAP_NS}company_{company_id}"
    commodity_lower = commodity.lower().replace(" ", "_")

    sparql = f"""
    PREFIX snowkap: <{SNOWKAP_NS}>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT ?supplier ?supLabel ?commodity ?comLabel ?scope3 WHERE {{
        GRAPH <{graph_uri}> {{
            ?supplier snowkap:suppliesTo <{company_uri}> .
            ?supplier rdfs:label ?supLabel .
            <{company_uri}> snowkap:dependsOnCommodity ?commodity .
            ?commodity rdfs:label ?comLabel .
            FILTER(CONTAINS(LCASE(STR(?comLabel)), "{commodity_lower}"))
            OPTIONAL {{ ?supplier snowkap:scope3Category ?scope3 }}
        }}
    }}
    """
    try:
        result = await jena_client.query(sparql)
        bindings = result.get("results", {}).get("bindings", [])
        return [
            {
                "supplier": b["supLabel"]["value"],
                "supplier_uri": b["supplier"]["value"],
                "commodity": b["comLabel"]["value"],
                "scope3_category": b.get("scope3", {}).get("value"),
            }
            for b in bindings
        ]
    except Exception as e:
        logger.error("supply_chain_query_failed", error=str(e))
        return []
