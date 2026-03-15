"""Graph builder — feeds from Apache Jena knowledge graph for MiroFish seed data.

Per MASTER_BUILD_PLAN Phase 4:
- Jena SPARQL → MiroFish GraphRAG seed
- Extract company subgraph from tenant's Jena named graph
- Build seed context for simulation agents
"""

import httpx
import structlog

from prediction.config import mirofish_settings

logger = structlog.get_logger()

SNOWKAP_NS = "http://snowkap.com/ontology/esg#"


async def extract_company_subgraph(
    company_id: str,
    tenant_id: str,
) -> dict:
    """Extract a company's knowledge subgraph from Jena for simulation seeding.

    Retrieves:
    - Company properties (industry, domain, SASB category)
    - Facility locations
    - Supply chain links (suppliers, commodities)
    - Material issues
    - Framework alignments
    - Geographic risk zones
    """
    graph_uri = f"urn:snowkap:tenant:{tenant_id}"
    company_uri = f"{SNOWKAP_NS}company_{company_id}"
    sparql_url = f"{mirofish_settings.JENA_FUSEKI_URL}/{mirofish_settings.JENA_DATASET}/sparql"

    subgraph: dict = {
        "company_uri": company_uri,
        "company_id": company_id,
        "tenant_id": tenant_id,
        "properties": {},
        "facilities": [],
        "suppliers": [],
        "commodities": [],
        "material_issues": [],
        "frameworks": [],
        "geographic_regions": [],
        "industry": None,
    }

    # Query company properties + relationships
    sparql = f"""
    PREFIX snowkap: <{SNOWKAP_NS}>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT ?predicate ?object ?objectLabel WHERE {{
        GRAPH <{graph_uri}> {{
            <{company_uri}> ?predicate ?object .
            OPTIONAL {{ ?object rdfs:label ?objectLabel }}
        }}
    }}
    """

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                sparql_url,
                params={"query": sparql, "default-graph-uri": graph_uri},
                headers={"Accept": "application/sparql-results+json"},
            )
            response.raise_for_status()
            bindings = response.json().get("results", {}).get("bindings", [])

            for b in bindings:
                pred = b["predicate"]["value"]
                obj = b["object"]["value"]
                label = b.get("objectLabel", {}).get("value", _uri_label(obj))

                if "belongsToIndustry" in pred:
                    subgraph["industry"] = label
                elif "hasFacility" in pred:
                    subgraph["facilities"].append({"uri": obj, "name": label})
                elif "dependsOnCommodity" in pred:
                    subgraph["commodities"].append({"uri": obj, "name": label})
                elif "hasMaterialIssue" in pred:
                    subgraph["material_issues"].append(label)
                elif "reportsUnder" in pred:
                    subgraph["frameworks"].append(label)
                elif "locatedIn" in pred:
                    subgraph["geographic_regions"].append(label)
                elif "label" in pred:
                    subgraph["properties"]["name"] = obj
                elif "domain" in pred:
                    subgraph["properties"]["domain"] = obj
                elif "sasbCategory" in pred:
                    subgraph["properties"]["sasb_category"] = obj

    except httpx.HTTPError as e:
        logger.error("subgraph_extraction_failed", company_id=company_id, error=str(e))

    # Query suppliers separately
    supplier_sparql = f"""
    PREFIX snowkap: <{SNOWKAP_NS}>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT ?supplier ?supLabel ?commodity ?comLabel WHERE {{
        GRAPH <{graph_uri}> {{
            ?supplier snowkap:suppliesTo <{company_uri}> .
            ?supplier rdfs:label ?supLabel .
            OPTIONAL {{
                <{company_uri}> snowkap:dependsOnCommodity ?commodity .
                ?commodity rdfs:label ?comLabel .
            }}
        }}
    }}
    """

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                sparql_url,
                params={"query": supplier_sparql},
                headers={"Accept": "application/sparql-results+json"},
            )
            response.raise_for_status()
            bindings = response.json().get("results", {}).get("bindings", [])
            for b in bindings:
                subgraph["suppliers"].append({
                    "uri": b["supplier"]["value"],
                    "name": b["supLabel"]["value"],
                    "commodity": b.get("comLabel", {}).get("value"),
                })
    except httpx.HTTPError:
        pass

    logger.info(
        "company_subgraph_extracted",
        company_id=company_id,
        facilities=len(subgraph["facilities"]),
        suppliers=len(subgraph["suppliers"]),
        issues=len(subgraph["material_issues"]),
    )
    return subgraph


async def extract_causal_context(
    article_id: str,
    company_id: str,
    tenant_id: str,
) -> dict:
    """Extract the causal chain context between an article and company from Jena."""
    graph_uri = f"urn:snowkap:tenant:{tenant_id}"
    sparql_url = f"{mirofish_settings.JENA_FUSEKI_URL}/{mirofish_settings.JENA_DATASET}/sparql"

    # This would query the causal chain triples stored in Jena
    # For now, the causal chain data comes from PostgreSQL
    return {
        "article_id": article_id,
        "company_id": company_id,
        "tenant_id": tenant_id,
        "context": "from_database",
    }


def build_seed_document(
    company_subgraph: dict,
    article_data: dict,
    causal_chain_data: dict | None = None,
) -> str:
    """Build a structured seed document for MiroFish agent context.

    This becomes the shared knowledge base that all simulation agents receive.
    """
    parts = [
        f"# Company Profile: {company_subgraph['properties'].get('name', 'Unknown')}",
        f"Industry: {company_subgraph.get('industry', 'Unknown')}",
        f"SASB Category: {company_subgraph['properties'].get('sasb_category', 'Unknown')}",
        "",
    ]

    if company_subgraph["facilities"]:
        parts.append("## Facilities")
        for f in company_subgraph["facilities"]:
            parts.append(f"- {f['name']}")
        parts.append("")

    if company_subgraph["suppliers"]:
        parts.append("## Supply Chain")
        for s in company_subgraph["suppliers"]:
            line = f"- {s['name']}"
            if s.get("commodity"):
                line += f" (commodity: {s['commodity']})"
            parts.append(line)
        parts.append("")

    if company_subgraph["material_issues"]:
        parts.append(f"## Material ESG Issues")
        for issue in company_subgraph["material_issues"]:
            parts.append(f"- {issue}")
        parts.append("")

    if company_subgraph["frameworks"]:
        parts.append(f"## Reporting Frameworks: {', '.join(company_subgraph['frameworks'])}")
        parts.append("")

    parts.append(f"## News Event")
    parts.append(f"**{article_data.get('title', '')}**")
    parts.append(article_data.get("summary", ""))
    parts.append("")

    if causal_chain_data:
        chain_path = causal_chain_data.get("chain_path", [])
        if chain_path:
            parts.append(f"## Causal Impact Chain")
            parts.append(f"Path: {' → '.join(chain_path)}")
            parts.append(f"Hops: {causal_chain_data.get('hops', 0)}")
            parts.append(f"Impact Score: {causal_chain_data.get('impact_score', 0):.2f}")
            parts.append(f"Relationship: {causal_chain_data.get('relationship_type', 'unknown')}")
            parts.append("")

    return "\n".join(parts)


def _uri_label(uri: str) -> str:
    """Extract label from URI."""
    if "#" in uri:
        return uri.split("#")[-1].replace("_", " ")
    if "/" in uri:
        return uri.rsplit("/", 1)[-1].replace("_", " ")
    return uri
