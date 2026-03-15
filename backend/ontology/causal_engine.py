"""Causal chain traversal and impact scoring engine.

Per MASTER_BUILD_PLAN Phase 3.2:
- BFS/DFS from news entity to company node, max 4 hops
- Impact scoring: decay function per hop (direct=1.0, 1-hop=0.7, 2-hop=0.4, 3-hop=0.2)
- Path explanation: human-readable causal chain

Per CLAUDE.md: Causal Relationship Types:
  directOperational (0-hop), supplyChainUpstream (1-hop), supplyChainDownstream (1-hop),
  workforceIndirect (2-hop), regulatoryContagion (1-hop), geographicProximity (0-hop),
  industrySpillover (1-hop), commodityChain (3-hop)
"""

from collections import deque
from dataclasses import dataclass, field

import structlog

from backend.ontology.jena_client import jena_client

logger = structlog.get_logger()

# Decay per hop per MASTER_BUILD_PLAN
HOP_DECAY = {0: 1.0, 1: 0.7, 2: 0.4, 3: 0.2, 4: 0.1}

# Causal relationship types per CLAUDE.md
RELATIONSHIP_TYPES = {
    "directOperational": {"typical_hops": 0, "description": "Direct operational impact"},
    "supplyChainUpstream": {"typical_hops": 1, "description": "Upstream supply chain impact"},
    "supplyChainDownstream": {"typical_hops": 1, "description": "Downstream supply chain impact"},
    "workforceIndirect": {"typical_hops": 2, "description": "Indirect workforce impact"},
    "regulatoryContagion": {"typical_hops": 1, "description": "Regulatory contagion"},
    "geographicProximity": {"typical_hops": 0, "description": "Geographic proximity impact"},
    "industrySpillover": {"typical_hops": 1, "description": "Industry spillover effect"},
    "commodityChain": {"typical_hops": 3, "description": "Commodity chain impact"},
}

MAX_HOPS = 4

SNOWKAP_NS = "http://snowkap.com/ontology/esg#"

# Map Jena predicate URIs to relationship types
PREDICATE_TO_RELATIONSHIP = {
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
    """Extract a human-readable label from a URI."""
    if "#" in uri:
        return uri.split("#")[-1].replace("_", " ")
    if "/" in uri:
        return uri.rsplit("/", 1)[-1].replace("_", " ")
    return uri


def classify_relationship(edge_uris: list[str]) -> str:
    """Determine the dominant relationship type from a list of predicate URIs."""
    for uri in edge_uris:
        if uri in PREDICATE_TO_RELATIONSHIP:
            return PREDICATE_TO_RELATIONSHIP[uri]
    return "directOperational"


def generate_explanation(path: CausalPath) -> str:
    """Generate human-readable causal chain explanation.

    Per MASTER_BUILD_PLAN example:
    "LPG prices → cooking fuel → truck drivers → your fleet welfare costs"
    """
    if not path.nodes:
        return ""
    return " → ".join(path.nodes)


async def find_causal_chains(
    source_entity: str,
    target_company_id: str,
    tenant_id: str,
    max_hops: int = MAX_HOPS,
) -> list[CausalPath]:
    """BFS traversal from news entity to company node in the ontology graph.

    Algorithm:
    1. Resolve source_entity to a URI in the tenant's Jena graph
    2. Resolve target_company_id to its company URI
    3. BFS outward from source, following all causal relationships
    4. At each hop, check if target company is reachable
    5. Record all paths up to max_hops
    6. Score each path with decay function and generate explanations
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

    # Deduplicate by node sequence
    seen = set()
    unique_paths = []
    for p in paths:
        key = tuple(p.node_uris)
        if key not in seen:
            seen.add(key)
            unique_paths.append(p)

    # Sort by impact score descending
    unique_paths.sort(key=lambda p: p.impact_score, reverse=True)

    logger.info("causal_chains_found", count=len(unique_paths), source=source_entity)
    return unique_paths


async def _resolve_entity(entity_text: str, tenant_id: str) -> list[str]:
    """Resolve an entity text to URIs in the Jena graph via label matching."""
    graph_uri = jena_client._tenant_graph(tenant_id)

    # Try exact label match first, then partial
    sparql = f"""
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX snowkap: <{SNOWKAP_NS}>
    SELECT DISTINCT ?entity WHERE {{
        GRAPH <{graph_uri}> {{
            ?entity rdfs:label ?label .
            FILTER(CONTAINS(LCASE(STR(?label)), LCASE("{_escape_sparql(entity_text)}")))
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
    """BFS from source to target in the Jena knowledge graph."""
    graph_uri = jena_client._tenant_graph(tenant_id)

    # Use SPARQL property paths for efficient multi-hop traversal
    paths: list[CausalPath] = []

    # Check each hop distance (0 to max_hops)
    for hops in range(max_hops + 1):
        if hops == 0:
            # Direct connection
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
                    FILTER(?mid1 != ?mid2 && ?mid2 != ?mid3)
                    FILTER(?mid1 != <{source_uri}> && ?mid3 != <{target_uri}>)
                }}
            }}
            LIMIT 10
            """
        else:
            # 4-hop: use property path with max length
            sparql = f"""
            PREFIX snowkap: <{SNOWKAP_NS}>
            SELECT ?path WHERE {{
                GRAPH <{graph_uri}> {{
                    <{source_uri}> (snowkap:|!snowkap:){{1,4}} <{target_uri}> .
                    BIND("exists" AS ?path)
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

    return None


def _escape_sparql(text: str) -> str:
    """Escape special characters for SPARQL string literals."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")


async def find_all_impacts_for_entity(
    entity_text: str,
    tenant_id: str,
    max_hops: int = MAX_HOPS,
) -> list[dict]:
    """Find all companies impacted by an entity, with causal chains.

    Used for: "Show me all paths from [news event] to [any company in my tenant]"
    """
    graph_uri = jena_client._tenant_graph(tenant_id)

    # First, resolve entity
    entity_uris = await _resolve_entity(entity_text, tenant_id)
    if not entity_uris:
        return []

    # Find all Company nodes in the tenant graph
    sparql = f"""
    PREFIX snowkap: <{SNOWKAP_NS}>
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
                    "all_paths_count": len(paths),
                    "max_impact_score": best_path.impact_score,
                    "min_hops": min(p.hops for p in paths),
                })

    impacts.sort(key=lambda x: x["max_impact_score"], reverse=True)
    return impacts
