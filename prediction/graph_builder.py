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
    """Extract the causal chain context between an article and company from Jena.

    Stage 4.2: Real SPARQL queries replace the hardcoded stub.
    Queries:
    1. All paths from article entities to company (via causal predicates)
    2. Framework indicators linked to entities in the chain
    3. Material issues along the path
    """
    graph_uri = f"urn:snowkap:tenant:{tenant_id}"
    company_uri = f"{SNOWKAP_NS}company_{company_id}"
    sparql_url = f"{mirofish_settings.JENA_FUSEKI_URL}/{mirofish_settings.JENA_DATASET}/sparql"

    result: dict = {
        "article_id": article_id,
        "company_id": company_id,
        "tenant_id": tenant_id,
        "chain_nodes": [],
        "chain_edges": [],
        "frameworks": [],
        "material_issues": [],
    }

    # Query 1: Causal chain paths (up to 3 hops) from any entity to the company
    chain_sparql = f"""
    PREFIX snowkap: <{SNOWKAP_NS}>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT ?node1 ?n1Label ?edge1 ?node2 ?n2Label ?edge2 ?node3 ?n3Label WHERE {{
        GRAPH <{graph_uri}> {{
            ?node1 ?edge1 ?node2 .
            ?node1 rdfs:label ?n1Label .
            ?node2 rdfs:label ?n2Label .
            FILTER(?node2 = <{company_uri}> || EXISTS {{ ?node2 ?edge2 ?node3 . ?node3 rdfs:label ?n3Label . FILTER(?node3 = <{company_uri}>) }})
            FILTER(?edge1 != rdfs:label && ?edge1 != rdf:type)
        }}
    }}
    LIMIT 20
    """

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                sparql_url,
                params={"query": chain_sparql, "default-graph-uri": graph_uri},
                headers={"Accept": "application/sparql-results+json"},
            )
            response.raise_for_status()
            bindings = response.json().get("results", {}).get("bindings", [])

            seen_nodes: set[str] = set()
            for b in bindings:
                for node_key, label_key in [("node1", "n1Label"), ("node2", "n2Label"), ("node3", "n3Label")]:
                    if node_key in b and b[node_key]["value"] not in seen_nodes:
                        seen_nodes.add(b[node_key]["value"])
                        result["chain_nodes"].append({
                            "uri": b[node_key]["value"],
                            "label": b.get(label_key, {}).get("value", _uri_label(b[node_key]["value"])),
                        })
                for edge_key in ["edge1", "edge2"]:
                    if edge_key in b:
                        result["chain_edges"].append(_uri_label(b[edge_key]["value"]))
    except httpx.HTTPError as e:
        logger.warning("causal_context_chain_query_failed", error=str(e))

    # Query 2: Framework indicators linked to entities in the chain
    fw_sparql = f"""
    PREFIX snowkap: <{SNOWKAP_NS}>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?framework ?indicator WHERE {{
        GRAPH <{graph_uri}> {{
            ?issue snowkap:reportsUnder ?fw .
            ?fw rdfs:label ?framework .
            <{company_uri}> snowkap:hasMaterialIssue ?issue .
            OPTIONAL {{ ?issue snowkap:frameworkIndicator ?indicator }}
        }}
    }}
    """

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                sparql_url,
                params={"query": fw_sparql, "default-graph-uri": graph_uri},
                headers={"Accept": "application/sparql-results+json"},
            )
            response.raise_for_status()
            bindings = response.json().get("results", {}).get("bindings", [])
            for b in bindings:
                fw = b.get("framework", {}).get("value", "")
                indicator = b.get("indicator", {}).get("value")
                entry = fw if not indicator else f"{fw}:{indicator}"
                if entry and entry not in result["frameworks"]:
                    result["frameworks"].append(entry)
    except httpx.HTTPError:
        pass

    # Query 3: Material issues for the company
    issues_sparql = f"""
    PREFIX snowkap: <{SNOWKAP_NS}>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT ?issueLabel WHERE {{
        GRAPH <{graph_uri}> {{
            <{company_uri}> snowkap:hasMaterialIssue ?issue .
            ?issue rdfs:label ?issueLabel .
        }}
    }}
    """

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                sparql_url,
                params={"query": issues_sparql, "default-graph-uri": graph_uri},
                headers={"Accept": "application/sparql-results+json"},
            )
            response.raise_for_status()
            bindings = response.json().get("results", {}).get("bindings", [])
            result["material_issues"] = [b["issueLabel"]["value"] for b in bindings]
    except httpx.HTTPError:
        pass

    logger.info(
        "causal_context_extracted",
        article_id=article_id,
        company_id=company_id,
        chain_nodes=len(result["chain_nodes"]),
        frameworks=len(result["frameworks"]),
    )
    return result


def build_seed_document(
    company_subgraph: dict,
    article_data: dict,
    causal_chain_data: dict | None = None,
    zep_memory: dict | None = None,
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
            parts.append("## Causal Impact Chain")
            parts.append(f"Path: {' → '.join(chain_path)}")
            parts.append(f"Hops: {causal_chain_data.get('hops', 0)}")
            parts.append(f"Impact Score: {causal_chain_data.get('impact_score', 0):.2f}")
            parts.append(f"Relationship: {causal_chain_data.get('relationship_type', 'unknown')}")
            parts.append("")

        # Stage 4.2: Include Jena-sourced chain nodes if available
        chain_nodes = causal_chain_data.get("chain_nodes", [])
        if chain_nodes:
            parts.append("## Knowledge Graph Chain Nodes")
            for node in chain_nodes[:10]:
                parts.append(f"- {node.get('label', node.get('uri', 'unknown'))}")
            parts.append("")

        chain_frameworks = causal_chain_data.get("frameworks", [])
        if chain_frameworks:
            parts.append(f"## Chain Framework Alignment: {', '.join(chain_frameworks)}")
            parts.append("")

    # Stage 4.1: Include Zep memory (prior simulation context)
    if zep_memory:
        parts.append("## Prior Simulation Intelligence")
        if zep_memory.get("context"):
            parts.append(f"Context: {zep_memory['context'][:500]}")
        facts = zep_memory.get("facts", [])
        if facts:
            for fact in facts[:5]:
                parts.append(f"- {fact}")
        parts.append("")

    return "\n".join(parts)


def _uri_label(uri: str) -> str:
    """Extract label from URI."""
    if "#" in uri:
        return uri.split("#")[-1].replace("_", " ")
    if "/" in uri:
        return uri.rsplit("/", 1)[-1].replace("_", " ")
    return uri
