"""Causal chain traversal and impact scoring engine.

Per MASTER_BUILD_PLAN Phase 3.2:
- BFS/DFS from news entity to company node, max 4 hops
- Impact scoring: decay function per hop (direct=1.0, 1-hop=0.7, 2-hop=0.4, 3-hop=0.2)
- Path explanation: human-readable causal chain

Stage 2.4: Fix 4-hop SPARQL property path syntax. Edge-aware dedup.
           Multi-path reporting (return up to 5 paths, not just best).
Stage 2.5: Add 9 new relationship types (total: 17).
"""

from dataclasses import dataclass, field

import structlog

from backend.ontology.jena_client import jena_client

logger = structlog.get_logger()

# Decay per hop per MASTER_BUILD_PLAN
HOP_DECAY = {0: 1.0, 1: 0.7, 2: 0.4, 3: 0.2, 4: 0.1}

# Stage 2.5: 17 causal relationship types (was 8)
RELATIONSHIP_TYPES = {
    # Original 8
    "directOperational": {"typical_hops": 0, "description": "Direct operational impact"},
    "supplyChainUpstream": {"typical_hops": 1, "description": "Upstream supply chain impact"},
    "supplyChainDownstream": {"typical_hops": 1, "description": "Downstream supply chain impact"},
    "workforceIndirect": {"typical_hops": 2, "description": "Indirect workforce impact"},
    "regulatoryContagion": {"typical_hops": 1, "description": "Regulatory contagion"},
    "geographicProximity": {"typical_hops": 0, "description": "Geographic proximity impact"},
    "industrySpillover": {"typical_hops": 1, "description": "Industry spillover effect"},
    "commodityChain": {"typical_hops": 3, "description": "Commodity chain impact"},
    # Stage 2.5: 9 new types
    "waterSharedBasin": {"typical_hops": 1, "description": "Shared water basin exposure"},
    "pollutionDispersion": {"typical_hops": 1, "description": "Pollution dispersion pathway"},
    "climateRiskExposure": {"typical_hops": 0, "description": "Climate risk zone exposure"},
    "laborContractor": {"typical_hops": 2, "description": "Labor contractor chain"},
    "communityAffected": {"typical_hops": 1, "description": "Community affected by operations"},
    "regulatoryJurisdiction": {"typical_hops": 1, "description": "Shared regulatory jurisdiction"},
    "ownershipChain": {"typical_hops": 2, "description": "Corporate ownership chain"},
    "investorExposure": {"typical_hops": 1, "description": "Investor portfolio exposure"},
    "customerConcentration": {"typical_hops": 1, "description": "Customer concentration risk"},
}

MAX_HOPS = 4
MAX_PATHS = 5  # Stage 2.4: return up to 5 paths

SNOWKAP_NS = "http://snowkap.com/ontology/esg#"

# Map Jena predicate URIs to relationship types (expanded for Stage 2.5)
PREDICATE_TO_RELATIONSHIP = {
    # Original mappings
    f"{SNOWKAP_NS}directlyImpacts": "directOperational",
    f"{SNOWKAP_NS}indirectlyImpacts": "workforceIndirect",
    f"{SNOWKAP_NS}suppliesTo": "supplyChainUpstream",
    f"{SNOWKAP_NS}sourcesFrom": "supplyChainDownstream",
    f"{SNOWKAP_NS}locatedIn": "geographicProximity",
    f"{SNOWKAP_NS}regulatedBy": "regulatoryContagion",
    f"{SNOWKAP_NS}competessWith": "industrySpillover",
    f"{SNOWKAP_NS}dependsOnCommodity": "commodityChain",
    f"{SNOWKAP_NS}hasFacility": "geographicProximity",
    f"{SNOWKAP_NS}belongsToIndustry": "industrySpillover",
    f"{SNOWKAP_NS}hasMaterialIssue": "directOperational",
    f"{SNOWKAP_NS}employsWorkforce": "workforceIndirect",
    # Stage 2.5: new predicate mappings
    f"{SNOWKAP_NS}sharesWaterBasin": "waterSharedBasin",
    f"{SNOWKAP_NS}pollutionPathway": "pollutionDispersion",
    f"{SNOWKAP_NS}climateExposure": "climateRiskExposure",
    f"{SNOWKAP_NS}laborContractedBy": "laborContractor",
    f"{SNOWKAP_NS}affectsCommunity": "communityAffected",
    f"{SNOWKAP_NS}sameJurisdiction": "regulatoryJurisdiction",
    f"{SNOWKAP_NS}ownedBy": "ownershipChain",
    f"{SNOWKAP_NS}investedIn": "investorExposure",
    f"{SNOWKAP_NS}customerOf": "customerConcentration",
    f"{SNOWKAP_NS}reportsUnder": "directOperational",
    f"{SNOWKAP_NS}frameworkIndicator": "directOperational",
    # Reverse edges for bidirectional BFS traversal
    f"{SNOWKAP_NS}sourcesFrom": "supplyChainDownstream",
    f"{SNOWKAP_NS}provides": "supplyChainUpstream",
    f"{SNOWKAP_NS}belongsToCompany": "directOperational",
    f"{SNOWKAP_NS}hasFacilityIn": "geographicProximity",
    f"{SNOWKAP_NS}sameCompany": "directOperational",
}


@dataclass
class CausalPath:
    """A single causal chain path from news entity to company."""
    nodes: list[str] = field(default_factory=list)
    node_uris: list[str] = field(default_factory=list)
    edges: list[str] = field(default_factory=list)
    edge_uris: list[str] = field(default_factory=list)
    hops: int = 0
    relationship_type: str = "directOperational"
    impact_score: float = 1.0
    explanation: str = ""
    esg_pillar: str | None = None
    frameworks: list[str] = field(default_factory=list)


def calculate_impact(hops: int, base_score: float = 1.0) -> float:
    """Calculate decayed impact score based on hop count."""
    decay = HOP_DECAY.get(hops, 0.05)
    return round(base_score * decay, 3)


def _uri_to_label(uri: str) -> str:
    """Extract a human-readable label from a URI.

    Strips UUIDs and technical prefixes to produce clean display names.
    """
    if "#" in uri:
        fragment = uri.split("#")[-1]
    elif "/" in uri:
        fragment = uri.rsplit("/", 1)[-1]
    else:
        fragment = uri

    # Strip common prefixes (company_, supplier_, facility_, commodity_, region_, competitor_)
    import re
    cleaned = re.sub(r"^(company|supplier|facility|commodity|region|competitor|industry|issue)_", "", fragment)
    # Strip UUIDs (8-4-4-4-12 hex pattern)
    cleaned = re.sub(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", "", cleaned)
    # Clean up underscores and extra spaces
    cleaned = cleaned.replace("_", " ").strip().strip("-").strip()

    return cleaned if cleaned else fragment.replace("_", " ")


def classify_relationship(edge_uris: list[str]) -> str:
    """Determine the dominant relationship type from a list of predicate URIs."""
    for uri in edge_uris:
        if uri in PREDICATE_TO_RELATIONSHIP:
            return PREDICATE_TO_RELATIONSHIP[uri]
    return "directOperational"


def generate_explanation(path: CausalPath) -> str:
    """Generate human-readable causal chain explanation."""
    if not path.nodes:
        return ""
    return " -> ".join(path.nodes)


async def find_causal_chains(
    source_entity: str,
    target_company_id: str,
    tenant_id: str,
    max_hops: int = MAX_HOPS,
) -> list[CausalPath]:
    """BFS traversal from news entity to company node in the ontology graph.

    Stage 2.4: Returns up to MAX_PATHS paths with edge-aware deduplication.
    """
    logger.info(
        "causal_chain_search",
        source=source_entity,
        target=target_company_id,
        tenant_id=tenant_id,
        max_hops=max_hops,
    )

    # Resolve source entity URI via SPARQL text search
    source_uris = await _resolve_entity(source_entity, tenant_id)
    if not source_uris:
        logger.info("causal_chain_no_source", entity=source_entity)
        return []

    # Target company URI
    target_uri = f"{SNOWKAP_NS}company_{target_company_id}"

    paths: list[CausalPath] = []

    for source_uri in source_uris[:3]:  # Try top 3 matches
        found = await _bfs_paths(source_uri, target_uri, tenant_id, max_hops)
        paths.extend(found)

    # Stage 2.4: Edge-aware dedup (paths via different relationship types are distinct)
    seen = set()
    unique_paths = []
    for p in paths:
        # Key includes both nodes AND edges for edge-aware dedup
        key = (tuple(p.node_uris), tuple(p.edge_uris))
        if key not in seen:
            seen.add(key)
            unique_paths.append(p)

    # Sort by impact score descending, return up to MAX_PATHS
    unique_paths.sort(key=lambda p: p.impact_score, reverse=True)
    unique_paths = unique_paths[:MAX_PATHS]

    # Stage 3.2: Enrich paths with framework data from Jena
    unique_paths = await _enrich_paths_with_frameworks(unique_paths, tenant_id)

    logger.info("causal_chains_found", count=len(unique_paths), source=source_entity)
    return unique_paths


async def _resolve_entity(entity_text: str, tenant_id: str) -> list[str]:
    """Resolve an entity text to URIs in the Jena graph via label matching.

    Also follows sameCompany links to find the canonical company URI.
    """
    graph_uri = jena_client._tenant_graph(tenant_id)

    sparql = f"""
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX snowkap: <{SNOWKAP_NS}>
    SELECT DISTINCT ?entity WHERE {{
        GRAPH <{graph_uri}> {{
            {{
                ?entity rdfs:label ?label .
                FILTER(CONTAINS(LCASE(STR(?label)), LCASE("{_escape_sparql(entity_text)}")))
            }}
            UNION
            {{
                ?alias rdfs:label ?label .
                FILTER(CONTAINS(LCASE(STR(?label)), LCASE("{_escape_sparql(entity_text)}")))
                ?alias snowkap:sameCompany ?entity .
            }}
        }}
    }}
    LIMIT 5
    """
    try:
        result = await jena_client.query(sparql)
        bindings = result.get("results", {}).get("bindings", [])
        return [b["entity"]["value"] for b in bindings]
    except Exception as e:
        logger.error("entity_resolve_failed", entity=entity_text, error=str(e))
        return []


async def _bfs_paths(
    source_uri: str,
    target_uri: str,
    tenant_id: str,
    max_hops: int,
) -> list[CausalPath]:
    """BFS from source to target in the Jena knowledge graph.

    Stage 2.4: Fixed 4-hop property path syntax. Returns multiple paths per hop level.
    """
    graph_uri = jena_client._tenant_graph(tenant_id)
    paths: list[CausalPath] = []

    for hops in range(max_hops + 1):
        if hops == 0:
            sparql = f"""
            PREFIX snowkap: <{SNOWKAP_NS}>
            SELECT ?rel WHERE {{
                GRAPH <{graph_uri}> {{
                    <{source_uri}> ?rel <{target_uri}> .
                }}
            }}
            """
        elif hops == 1:
            sparql = f"""
            PREFIX snowkap: <{SNOWKAP_NS}>
            SELECT ?mid ?rel1 ?rel2 WHERE {{
                GRAPH <{graph_uri}> {{
                    <{source_uri}> ?rel1 ?mid .
                    ?mid ?rel2 <{target_uri}> .
                    FILTER(?mid != <{source_uri}> && ?mid != <{target_uri}>)
                }}
            }}
            LIMIT 20
            """
        elif hops == 2:
            sparql = f"""
            PREFIX snowkap: <{SNOWKAP_NS}>
            SELECT ?mid1 ?mid2 ?rel1 ?rel2 ?rel3 WHERE {{
                GRAPH <{graph_uri}> {{
                    <{source_uri}> ?rel1 ?mid1 .
                    ?mid1 ?rel2 ?mid2 .
                    ?mid2 ?rel3 <{target_uri}> .
                    FILTER(?mid1 != <{source_uri}> && ?mid2 != <{target_uri}>)
                    FILTER(?mid1 != ?mid2)
                }}
            }}
            LIMIT 15
            """
        elif hops == 3:
            sparql = f"""
            PREFIX snowkap: <{SNOWKAP_NS}>
            SELECT ?mid1 ?mid2 ?mid3 ?rel1 ?rel2 ?rel3 ?rel4 WHERE {{
                GRAPH <{graph_uri}> {{
                    <{source_uri}> ?rel1 ?mid1 .
                    ?mid1 ?rel2 ?mid2 .
                    ?mid2 ?rel3 ?mid3 .
                    ?mid3 ?rel4 <{target_uri}> .
                    FILTER(?mid1 != ?mid2 && ?mid2 != ?mid3 && ?mid1 != ?mid3)
                    FILTER(?mid1 != <{source_uri}> && ?mid3 != <{target_uri}>)
                }}
            }}
            LIMIT 10
            """
        else:
            # Stage 2.4: Fixed 4-hop property path syntax
            # Use explicit 4-hop pattern instead of broken property path
            sparql = f"""
            PREFIX snowkap: <{SNOWKAP_NS}>
            SELECT ?mid1 ?mid2 ?mid3 ?mid4 ?rel1 ?rel2 ?rel3 ?rel4 ?rel5 WHERE {{
                GRAPH <{graph_uri}> {{
                    <{source_uri}> ?rel1 ?mid1 .
                    ?mid1 ?rel2 ?mid2 .
                    ?mid2 ?rel3 ?mid3 .
                    ?mid3 ?rel4 ?mid4 .
                    ?mid4 ?rel5 <{target_uri}> .
                    FILTER(?mid1 != ?mid2 && ?mid2 != ?mid3 && ?mid3 != ?mid4 && ?mid1 != ?mid3 && ?mid1 != ?mid4 && ?mid2 != ?mid4)
                    FILTER(?mid1 != <{source_uri}> && ?mid4 != <{target_uri}>)
                }}
            }}
            LIMIT 5
            """

        try:
            result = await jena_client.query(sparql)
            bindings = result.get("results", {}).get("bindings", [])

            for b in bindings:
                path = _binding_to_path(b, source_uri, target_uri, hops)
                if path:
                    paths.append(path)

        except Exception as e:
            logger.warning("bfs_hop_failed", hops=hops, error=str(e))
            continue

    return paths


def _binding_to_path(
    binding: dict, source_uri: str, target_uri: str, hops: int,
) -> CausalPath | None:
    """Convert a SPARQL result binding to a CausalPath."""
    source_label = _uri_to_label(source_uri)
    target_label = _uri_to_label(target_uri)

    if hops == 0:
        rel = binding.get("rel", {}).get("value", "")
        relationship = PREDICATE_TO_RELATIONSHIP.get(rel, "directOperational")
        path = CausalPath(
            nodes=[source_label, target_label],
            node_uris=[source_uri, target_uri],
            edges=[_uri_to_label(rel)],
            edge_uris=[rel],
            hops=0,
            relationship_type=relationship,
            impact_score=calculate_impact(0),
        )
        path.explanation = generate_explanation(path)
        return path

    elif hops == 1:
        mid = binding.get("mid", {}).get("value", "")
        rel1 = binding.get("rel1", {}).get("value", "")
        rel2 = binding.get("rel2", {}).get("value", "")
        path = CausalPath(
            nodes=[source_label, _uri_to_label(mid), target_label],
            node_uris=[source_uri, mid, target_uri],
            edges=[_uri_to_label(rel1), _uri_to_label(rel2)],
            edge_uris=[rel1, rel2],
            hops=1,
            relationship_type=classify_relationship([rel1, rel2]),
            impact_score=calculate_impact(1),
        )
        path.explanation = generate_explanation(path)
        return path

    elif hops == 2:
        mid1 = binding.get("mid1", {}).get("value", "")
        mid2 = binding.get("mid2", {}).get("value", "")
        rel1 = binding.get("rel1", {}).get("value", "")
        rel2 = binding.get("rel2", {}).get("value", "")
        rel3 = binding.get("rel3", {}).get("value", "")
        path = CausalPath(
            nodes=[source_label, _uri_to_label(mid1), _uri_to_label(mid2), target_label],
            node_uris=[source_uri, mid1, mid2, target_uri],
            edges=[_uri_to_label(rel1), _uri_to_label(rel2), _uri_to_label(rel3)],
            edge_uris=[rel1, rel2, rel3],
            hops=2,
            relationship_type=classify_relationship([rel1, rel2, rel3]),
            impact_score=calculate_impact(2),
        )
        path.explanation = generate_explanation(path)
        return path

    elif hops == 3:
        mid1 = binding.get("mid1", {}).get("value", "")
        mid2 = binding.get("mid2", {}).get("value", "")
        mid3 = binding.get("mid3", {}).get("value", "")
        rel1 = binding.get("rel1", {}).get("value", "")
        rel2 = binding.get("rel2", {}).get("value", "")
        rel3 = binding.get("rel3", {}).get("value", "")
        rel4 = binding.get("rel4", {}).get("value", "")
        path = CausalPath(
            nodes=[
                source_label, _uri_to_label(mid1), _uri_to_label(mid2),
                _uri_to_label(mid3), target_label,
            ],
            node_uris=[source_uri, mid1, mid2, mid3, target_uri],
            edges=[
                _uri_to_label(rel1), _uri_to_label(rel2),
                _uri_to_label(rel3), _uri_to_label(rel4),
            ],
            edge_uris=[rel1, rel2, rel3, rel4],
            hops=3,
            relationship_type=classify_relationship([rel1, rel2, rel3, rel4]),
            impact_score=calculate_impact(3),
        )
        path.explanation = generate_explanation(path)
        return path

    elif hops == 4:
        mid1 = binding.get("mid1", {}).get("value", "")
        mid2 = binding.get("mid2", {}).get("value", "")
        mid3 = binding.get("mid3", {}).get("value", "")
        mid4 = binding.get("mid4", {}).get("value", "")
        rel1 = binding.get("rel1", {}).get("value", "")
        rel2 = binding.get("rel2", {}).get("value", "")
        rel3 = binding.get("rel3", {}).get("value", "")
        rel4 = binding.get("rel4", {}).get("value", "")
        rel5 = binding.get("rel5", {}).get("value", "")
        path = CausalPath(
            nodes=[
                source_label, _uri_to_label(mid1), _uri_to_label(mid2),
                _uri_to_label(mid3), _uri_to_label(mid4), target_label,
            ],
            node_uris=[source_uri, mid1, mid2, mid3, mid4, target_uri],
            edges=[
                _uri_to_label(rel1), _uri_to_label(rel2),
                _uri_to_label(rel3), _uri_to_label(rel4), _uri_to_label(rel5),
            ],
            edge_uris=[rel1, rel2, rel3, rel4, rel5],
            hops=4,
            relationship_type=classify_relationship([rel1, rel2, rel3, rel4, rel5]),
            impact_score=calculate_impact(4),
        )
        path.explanation = generate_explanation(path)
        return path

    return None


def _escape_sparql(text: str) -> str:
    """Escape special characters for SPARQL string literals.

    Handles all SPARQL 1.1 special characters to prevent injection.
    """
    # Order matters: escape backslash first
    text = text.replace("\\", "\\\\")
    text = text.replace('"', '\\"')
    text = text.replace("'", "\\'")
    text = text.replace("\n", "\\n")
    text = text.replace("\r", "\\r")
    text = text.replace("\t", "\\t")
    text = text.replace("\b", "\\b")
    text = text.replace("\f", "\\f")
    # Strip characters that could break out of string context
    text = text.replace("{", "").replace("}", "")
    text = text.replace("<", "").replace(">", "")
    return text


async def _query_entity_frameworks(entity_uri: str, tenant_id: str) -> list[str]:
    """Query framework relationships for an entity from Jena.

    Stage 3.2: After BFS discovers entities, query reportsUnder and frameworkIndicator.
    Returns framework codes like ["BRSR:P6", "GRI 305", "TCFD:Strategy"].
    """
    graph_uri = jena_client._tenant_graph(tenant_id)
    frameworks: list[str] = []

    # Query direct framework links via reportsUnder
    sparql_fw = f"""
    PREFIX snowkap: <{SNOWKAP_NS}>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?fw ?fwLabel WHERE {{
        GRAPH <{graph_uri}> {{
            <{entity_uri}> snowkap:reportsUnder ?fw_uri .
            ?fw_uri rdfs:label ?fwLabel .
            BIND(STR(?fwLabel) AS ?fw)
        }}
    }}
    LIMIT 20
    """
    try:
        result = await jena_client.query(sparql_fw)
        for b in result.get("results", {}).get("bindings", []):
            fw = b.get("fw", {}).get("value", "") or b.get("fwLabel", {}).get("value", "")
            if fw:
                frameworks.append(fw)
    except Exception as exc:
        logger.warning("causal_chain_framework_lookup_failed", error=str(exc))

    # Query material issue → framework indicator mappings
    sparql_ind = f"""
    PREFIX snowkap: <{SNOWKAP_NS}>
    SELECT ?indicator WHERE {{
        GRAPH <{graph_uri}> {{
            <{entity_uri}> snowkap:frameworkIndicator ?indicator .
        }}
    }}
    LIMIT 20
    """
    try:
        result = await jena_client.query(sparql_ind)
        for b in result.get("results", {}).get("bindings", []):
            indicator = b.get("indicator", {}).get("value", "")
            if indicator:
                frameworks.append(indicator)
    except Exception as exc:
        logger.warning("entity_impact_indicator_lookup_failed", error=str(exc))

    return list(set(frameworks))


async def _enrich_paths_with_frameworks(
    paths: list[CausalPath], tenant_id: str,
) -> list[CausalPath]:
    """Enrich causal paths with framework data from Jena.

    Stage 3.2: For each entity in the path, query framework relationships
    and merge into path.frameworks.
    """
    for path in paths:
        all_frameworks: list[str] = []
        for uri in path.node_uris:
            if uri.startswith(SNOWKAP_NS) or uri.startswith("http"):
                fws = await _query_entity_frameworks(uri, tenant_id)
                all_frameworks.extend(fws)
        path.frameworks = list(set(all_frameworks))
    return paths


async def find_all_impacts_for_entity(
    entity_text: str,
    tenant_id: str,
    max_hops: int = MAX_HOPS,
) -> list[dict]:
    """Find all companies impacted by an entity, with causal chains.

    Used for: "Show me all paths from [news event] to [any company in my tenant]"
    Stage 2.4: Returns up to MAX_PATHS paths per company.
    """
    graph_uri = jena_client._tenant_graph(tenant_id)

    # First, resolve entity
    entity_uris = await _resolve_entity(entity_text, tenant_id)
    if not entity_uris:
        return []

    # Find all Company nodes in the tenant graph
    sparql = f"""
    PREFIX snowkap: <{SNOWKAP_NS}>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?company ?label WHERE {{
        GRAPH <{graph_uri}> {{
            ?company a snowkap:Company .
            OPTIONAL {{ ?company rdfs:label ?label }}
        }}
    }}
    """
    try:
        result = await jena_client.query(sparql)
        companies = result.get("results", {}).get("bindings", [])
    except Exception:
        companies = []

    impacts = []
    for company_binding in companies:
        company_uri = company_binding["company"]["value"]
        company_label = company_binding.get("label", {}).get("value", _uri_to_label(company_uri))

        for source_uri in entity_uris[:2]:
            paths = await _bfs_paths(source_uri, company_uri, tenant_id, max_hops)
            if paths:
                best_path = max(paths, key=lambda p: p.impact_score)
                impacts.append({
                    "company_uri": company_uri,
                    "company_name": company_label,
                    "best_path": best_path,
                    "all_paths": paths[:MAX_PATHS],  # Stage 2.4: return multiple paths
                    "all_paths_count": len(paths),
                    "max_impact_score": best_path.impact_score,
                    "min_hops": min(p.hops for p in paths),
                })

    impacts.sort(key=lambda x: x["max_impact_score"], reverse=True)
    return impacts
